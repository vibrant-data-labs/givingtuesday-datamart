import pandas as pd
from givingtuesday_datamart.proofs.geographic_rank_audit import geographic_flags


def test_flags_require_strong_old_location_evidence_and_a_real_conflict():
    rows=pd.DataFrame([
        ('river fund','river fund','river fund','lynnwood','aberdeen',1.,.55),
        ('river fund','river fund','river fund','lynnwood','',1.,.55),
        ('river fund','river fund','river fund','lynnwood','lynnwood',1.,.55),
        ('river fund','river fund','river fund','lynnwood','aberdeen',.7,.4),
        ('river fund','river fund annex','river fund','lynnwood','aberdeen',1.,.55),
    ],columns=['full_name','before_full_name','after_full_name','before_addresscity_key','after_addresscity_key','before_addr_score','after_addr_score'])
    rows['addresscity_key']='lynnwood'
    for dim in ['state','zip']:
        for prefix in ['','before_','after_']:rows[prefix+'address'+dim+'_key']=''
    f=geographic_flags(rows)
    assert f.strong_location_loss.tolist()==[True,False,False,False,False]
