"""Predeclared geography-aware ranking; old cases are regression, fresh cases validation.

No production writes. Local support=city+state agreement or matching ZIP
with address score >=.90; state conflict overrides local support. Missing
geography is unknown. Order: exact name, geography, source, original score.
"""
import argparse
import hashlib
import inspect
import json
from pathlib import Path
import pandas as pd
from sqlalchemy import text
from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.proofs import bmf_policy_subset as base
from givingtuesday_datamart.proofs import rank_policy_subset as rp


def geo_tier(m,u,g):
    ui=m[gm.INDEX_COLS[0]];gi=m[gm.INDEX_COLS[1]]
    def values(frame,col,idx):
        normalized=frame[col].fillna('').str.lower().str.replace(r'[^a-z0-9]+',' ',regex=True).str.strip()
        return idx.map(normalized).fillna('')
    us,gs=values(u,'addressstate_key',ui),values(g,'addressstate_key',gi)
    uc,gc=values(u,'addresscity_key',ui),values(g,'addresscity_key',gi)
    uz,gz=values(u,'addresszip_key',ui),values(g,'addresszip_key',gi)
    agree=us.ne('')&gs.ne('')&us.eq(gs)
    conflict=us.ne('')&gs.ne('')&us.ne(gs)
    local=(agree&uc.ne('')&gc.ne('')&uc.eq(gc)) | (uz.ne('')&gz.ne('')&uz.eq(gz)&m.addr_score.ge(.90))
    tier=pd.Series(1,index=m.index)
    tier.loc[agree]=2;tier.loc[local]=3;tier.loc[conflict]=0
    return tier


def matches(f,u,g):
    m=gm.filter_match_rules(f,**gm.FINAL_FILTER_RULES).copy()
    m=gm.guard_government_names(m,g.full_name,u.full_name).drop_duplicates()
    m['match_tier']=pd.Series(gm.STATE_TIER,index=m.index).where(~gm._by_address(m),gm.ADDRESS_TIER)
    state=m[m.name_score.ge(.99)&m.state_score.eq(1)]
    owners=state.assign(_ein=state[gm.INDEX_COLS[0]].map(u.filerein_key)).groupby(gm.INDEX_COLS[1])._ein.nunique()
    m['_exact']=m[gm.INDEX_COLS[0]].map(u.full_name).eq(m[gm.INDEX_COLS[1]].map(g.full_name))
    m['_geo']=geo_tier(m,u,g)
    m['_source']=m[gm.INDEX_COLS[0]].map(u.source).ne('eo_bmf')
    m['_score']=2*m.name_score+m.addr_score
    m=m.sort_values(['_exact','_geo','_source','_score'],ascending=False,kind='mergesort').drop_duplicates(gm.INDEX_COLS[1])
    shared=m.match_tier.eq(gm.STATE_TIER)&m[gm.INDEX_COLS[1]].map(owners).fillna(1).gt(1)
    shared &=m[gm.INDEX_COLS[1]].map(~(g.address1_key+' '+g.address2_key).str.contains(gm._STREET)).astype(bool)
    m=m[~shared].drop(columns=['_exact','_geo','_source','_score'])
    n=gm.name_only_matches(u,g,m[gm.INDEX_COLS[1]]).assign(name_score=1.,addr_score=float('nan'))
    result=pd.concat([m.assign(match_name_words=pd.NA),n],ignore_index=True)
    result.match_name_words=result.match_name_words.astype('Int64')
    return result


def policy_hash():
    return hashlib.sha256((inspect.getsource(geo_tier)+inspect.getsource(matches)).encode()).hexdigest()


def freeze_fresh(previous,out):
    out.mkdir(parents=True,exist_ok=True)
    if (out/'manifest.json').exists():raise FileExistsError('Use a fresh validation directory')
    q=base.selection_sql().replace('::text, 0)', '::text, 42)')
    params={'one_in':100,'cohort':'a^','placeholder':base.PLACEHOLDER_REGEX}
    with get_session(config=datamart_config()) as s:
        s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        for setting in ("work_mem='64MB'","max_parallel_workers_per_gather=0","statement_timeout='10min'","temp_file_limit='4GB'"):
            s.execute(text('SET LOCAL '+setting))
        plan=[r[0] for r in s.execute(text('EXPLAIN '+q),params)]
        g=pd.read_sql_query(text(q),s.connection(),params=params)
    seen=pd.read_parquet(previous/'results.parquet',columns=gm._KEYS).drop_duplicates()
    mask=pd.MultiIndex.from_frame(g[gm._KEYS]).isin(pd.MultiIndex.from_frame(seen))
    g=g[~mask].reset_index(drop=True);g['evaluation_split']='fresh_validation'
    g['high_value']=False;g['labeled_funder']=False
    for name in rp.RISKS:g['risk_'+name]=False
    g.to_parquet(out/'grants.parquet',index=False)
    manifest={'policy_hash':policy_hash(),'seed':42,'one_in':100,'tuples':len(g),
              'rows':int(g.n_rows.sum()),'excluded_seen':int(mask.sum()),'overlap':0,
              'grants_sha256':base._digest(out/'grants.parquet'),'plans':plan}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({k:v for k,v in manifest.items() if k!='plans'}))


def run(previous,out,fresh=False):
    out.mkdir(parents=True,exist_ok=True)
    u=pd.read_parquet(previous/'universe.parquet')
    p=pd.read_parquet(previous/'production.parquet')
    g=pd.read_parquet(out/'grants.parquet' if fresh else previous/'grants.parquet')
    supplement=None
    if not fresh and (previous/'funder_supplement.parquet').exists():supplement=pd.read_parquet(previous/'funder_supplement.parquet')
    if fresh:
        meta=json.loads((out/'manifest.json').read_text())
        if meta['policy_hash']!=policy_hash() or meta['grants_sha256']!=base._digest(out/'grants.parquet'):raise RuntimeError('Frozen policy or inputs changed')
    g=g.merge(p[gm._KEYS+['production_ein']],on=gm._KEYS,how='left',validate='one_to_one');g.production_ein=g.production_ein.fillna('')
    print('Preparing',len(u),len(g),flush=True);gm.prepare(u);gm.prepare(g)
    if fresh:
        if not (out/'inputs.json').exists():
            (out/'inputs.json').write_text((out/'manifest.json').read_text())
        f=base.score(u,g,out,8)
    else:
        sig=json.loads((previous/'scores/signature.json').read_text())
        if sig['inputs']!=base._digest(previous/'inputs.json') or sig['matcher']!=base._digest(Path(gm.__file__)):raise RuntimeError('Old score cache is stale')
        f=pd.concat([pd.read_parquet(p) for p in sorted((previous/'scores').glob('*.parquet'))],ignore_index=True)
    def resolve(grants,features):
        baseline=base.comparison_rows(grants,u,gm.all_matches(features,u,grants),'current')
        if baseline.ein.ne(grants.production_ein).any():raise RuntimeError('Current resolver differs from production')
        open_=grants[~grants.placeholder]
        new=base.comparison_rows(grants,u,matches(features[features[gm.INDEX_COLS[1]].isin(open_.index)],u,open_),'name_geo_source')
        for frame in (baseline,new):frame['baseline_ein']=baseline.ein
        return [baseline,new]
    frames=resolve(g,f)
    if supplement is not None:
        e=supplement.merge(p[gm._KEYS+['production_ein']],on=gm._KEYS,how='left',validate='one_to_one');e.production_ein=e.production_ein.fillna('')
        gm.prepare(e);frames+=resolve(e,gm.score_slice(u,e))
    result=pd.concat(frames,ignore_index=True)
    # Coverage expects the complete control policy before the alternative.
    result=pd.concat([result[result.policy.eq('current')],result[result.policy.ne('current')]],ignore_index=True)
    result.to_parquet(out/'results.parquet',index=False)
    rp.summarize(result).to_csv(out/'summary.csv',index=False)
    result[result.full_name.isin(['johns hopkins university','duke university','progressive animal welfare society', 'notre dame high school','bates college','weta public television and radio'])].groupby(['policy','full_name','ein']).agg(rows=('n_rows','sum'),dollars=('dollars','sum')).to_csv(out/'diagnostics.csv')
    if not fresh:
        cov=rp.labeled_coverage(result,pd.read_parquet(previous/'funder_tuples.parquet'),pd.read_parquet(previous/'labels.parquet'),pd.read_parquet(previous/'output_pairs.parquet'),out/'labeled_changes.csv')
        cov.to_csv(out/'labeled_coverage.csv',index=False);print(cov.to_string(index=False))
    (out/'validation.json').write_text(json.dumps({'policy_hash':policy_hash(),'tuples':len(result)//2,'production_mismatches':0,'fresh':fresh},indent=2)+'\n')
    print('DONE',out,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['freeze','known','fresh']);p.add_argument('--previous',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    if a.command=='freeze':freeze_fresh(a.previous,a.out)
    else:run(a.previous,a.out,a.command=='fresh')
