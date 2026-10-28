"""Audit source-preference reassignments using frozen winner identities.

No database access or ranking changes. Compare best qualifying addresses
for the actual before/after winning name+source, not an arbitrary address
of that EIN. Flags identify review candidates, not measured false positives.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import pandas as pd
from givingtuesday_datamart import grant_matching as gm


def geographic_flags(rows: pd.DataFrame) -> pd.DataFrame:
    def nonempty_equal(a,b):
        return a.ne("") & b.ne("") & a.eq(b)
    def conflict(a,b):
        return a.ne("") & b.ne("") & a.ne(b)
    flags=pd.DataFrame(index=rows.index)
    flags['same_name']=rows.before_full_name.eq(rows.full_name)&rows.after_full_name.eq(rows.full_name)
    for dimension in ('city','state','zip'):
        grant=rows['address'+dimension+'_key'].fillna('').str.lower().str.strip()
        old=rows['before_address'+dimension+'_key'].fillna('').str.lower().str.strip()
        new=rows['after_address'+dimension+'_key'].fillna('').str.lower().str.strip()
        flags['lost_'+dimension+'_agreement']=nonempty_equal(grant,old)&conflict(grant,new)
    flags['strong_location_loss']=(flags.same_name & rows.before_addr_score.ge(.90)
        & (rows.before_addr_score-rows.after_addr_score).ge(.20)
        & (flags.lost_city_agreement|flags.lost_state_agreement|flags.lost_zip_agreement))
    flags['strong_city_state_loss']=flags.strong_location_loss & (flags.lost_city_agreement|flags.lost_state_agreement)
    flags['strong_zip_only_loss']=flags.strong_location_loss & ~flags.strong_city_state_loss
    return flags


def audit(directory: Path, after_policy: str = 'name_then_filing', universe_directory: Path | None = None) -> None:
    result=pd.read_parquet(directory/'results.parquet')
    current=result[result.policy.eq('current')].copy()
    after=result[result.policy.eq(after_policy)].copy()
    if current.duplicated(gm._KEYS).any() or after.duplicated(gm._KEYS).any():
        raise ValueError('Repeated recipient outcomes; audit requires unique tuples per policy')
    before=current[gm._KEYS+['ein','matched_name','source']].rename(columns={
        'ein':'before_ein','matched_name':'before_name','source':'before_source'})
    changed=after.merge(before,on=gm._KEYS,validate='one_to_one')
    changed=changed[changed.ein.ne('')&changed.before_ein.ne('')&changed.ein.ne(changed.before_ein)].reset_index(drop=True)
    # Saved results include categorical blocking columns with missing values.
    # Prepare from raw keys again rather than filling categorical NA with ''.
    changed=changed.drop(columns=['clean_zip','full_name','compare_name','compare_state','compare_addr','block_zip'],errors='ignore')
    changed['audit_id']=changed.index
    u=pd.read_parquet((universe_directory or directory)/'universe.parquet')
    u=u[u.filerein_key.isin(set(changed.ein)|set(changed.before_ein))].copy()
    gm.prepare(u);gm.prepare(changed)
    sides=[]
    for side,ein,name,source in [('before','before_ein','before_name','before_source'),('after','ein','matched_name','source')]:
        targets=changed[['audit_id',ein,name,source]].rename(columns={ein:'target_ein',name:'target_name',source:'target_source'})
        identities=u[['filerein_key','full_name','source']].assign(universe_index=u.index)
        pairs=targets.merge(identities,left_on=['target_ein','target_name','target_source'],
                            right_on=['filerein_key','full_name','source'])
        links=pd.MultiIndex.from_arrays([pairs.universe_index,pairs.audit_id])
        f=gm.pair_scores(links,u,changed)
        f.index=f.index.set_names(gm.INDEX_COLS)
        f=f.reset_index()
        # Respect the original ZIP/name candidate blocking too.
        ui=f[gm.INDEX_COLS[0]];gi=f[gm.INDEX_COLS[1]]
        blocked=(ui.map(u.full_name).eq(gi.map(changed.full_name))
                 | (ui.map(u.clean_zip).eq(gi.map(changed.clean_zip))
                    & ui.map(u.compare_addr).ne('') & gi.map(changed.compare_addr).ne('')))
        f=gm.filter_match_rules(f[blocked],**gm.FINAL_FILTER_RULES)
        f=gm.guard_government_names(f,changed.full_name,u.full_name)
        f=f.assign(combined=2*f.name_score+f.addr_score)
        if side=='after' and after_policy=='name_geo_source':
            from givingtuesday_datamart.proofs.geo_policy_test import geo_tier
            f['_geo']=geo_tier(f,u,changed)
            order=['_geo','combined',gm.INDEX_COLS[0]];directions=[False,False,True]
        else:
            order=['combined',gm.INDEX_COLS[0]];directions=[False,True]
        f=f.sort_values(order,ascending=directions,kind='mergesort').drop_duplicates(gm.INDEX_COLS[1])
        f=f.rename(columns={gm.INDEX_COLS[1]:'audit_id'})
        for col in ['full_name','address1_key','address2_key','addresscity_key','addressstate_key','addresszip_key']:
            f[side+'_'+col]=f[gm.INDEX_COLS[0]].map(u[col])
        f=f.rename(columns={'name_score':side+'_name_score','addr_score':side+'_addr_score'})
        cols=['audit_id',side+'_name_score',side+'_addr_score']+[c for c in f if c.startswith(side+'_address') or c==side+'_full_name']
        sides.append(f[cols])
    compared=changed
    for s in sides:compared=compared.merge(s,on='audit_id',how='inner',validate='one_to_one')
    flags=geographic_flags(compared)
    compared=pd.concat([compared,flags],axis=1)
    out=directory/'geographic_audit';out.mkdir(exist_ok=True)
    compared.to_parquet(out/'comparisons.parquet',index=False)
    flagged=compared[compared.strong_location_loss].sort_values('positive_dollars',ascending=False)
    columns=gm._KEYS+['evaluation_split','n_rows','dollars','positive_dollars','before_ein','ein','before_source','source',
        'strong_city_state_loss','strong_zip_only_loss',
        'before_name_score','after_name_score','before_addr_score','after_addr_score',
        'before_address1_key','before_addresscity_key','before_addressstate_key','before_addresszip_key',
        'after_address1_key','after_addresscity_key','after_addressstate_key','after_addresszip_key']
    flagged[columns].to_csv(out/'flagged_cases.csv',index=False)
    summary=[]
    for split,g in compared.groupby('evaluation_split'):
        for flag in flags.columns:
            selected=g[g[flag]]
            summary.append({'split':split,'flag':flag,'tuples':len(selected),'rows':int(selected.n_rows.sum()),
                            'positive_dollars':float(selected.positive_dollars.sum())})
    pd.DataFrame(summary).to_csv(out/'summary.csv',index=False)
    metadata={'changed_tuples':len(changed),'scored_both_sides':len(compared),
              'strong_location_loss_tuples':len(flagged),'flagged_rows':int(flagged.n_rows.sum()),
              'flagged_positive_dollars':float(flagged.positive_dollars.sum())}
    (out/'counts.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps(metadata));print(flagged[columns].head(20).to_string(index=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--after-policy',default='name_then_filing');p.add_argument('--universe-directory',type=Path)
    a=p.parse_args();audit(a.directory,a.after_policy,a.universe_directory)
