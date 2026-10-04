import csv, io, json, sys, urllib.parse, urllib.request, urllib.error, hashlib, re, time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/"data"; DATA.mkdir(exist_ok=True)
NOW_DT=datetime.now(timezone.utc); NOW=NOW_DT.isoformat()
ALL=["EUR","USD","CNY","JPY","GBP","INR","CAD","BRL","RUB","KRW","AUD","MXN","IDR","TRY","SAR","CHF","PLN","SEK","NOK","AED"]
AREA_TO_CUR={"USA":"USD","CHN":"CNY","JPN":"JPY","GBR":"GBP","IND":"INR","CAN":"CAD","BRA":"BRL","RUS":"RUB","KOR":"KRW","AUS":"AUD","MEX":"MXN","IDN":"IDR","TUR":"TRY","SAU":"SAR","CHE":"CHF","POL":"PLN","SWE":"SEK","NOR":"NOK","ARE":"AED"}
ILO=[("EAR_4MTH_SEX_NB_M","monthly"),("EAR_4MTH_SEX_NB_Q","quarterly"),("EAR_EMTA_SEX_NB_A","annual")]

def load(p,d):
    try:return json.loads(p.read_text(encoding="utf8"))
    except Exception:return d

def atomic_json(p,obj):
    tmp=p.with_suffix(p.suffix+".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    tmp.replace(p)

def get(url,headers=None,timeout=35,retries=3):
    h={"User-Agent":"GSWL-Data-Engine/4.2 (+https://github.com/khalisghilmanou96-dev/GSWL)","Accept":"*/*"}
    if headers:h.update(headers)
    last=None
    for attempt in range(retries):
        try:
            req=urllib.request.Request(url,headers=h)
            with urllib.request.urlopen(req,timeout=timeout) as r:return r.read(),dict(r.headers)
        except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError) as e:
            last=e
            code=getattr(e,"code",None)
            if code and code not in (408,429,500,502,503,504):break
            if attempt<retries-1:time.sleep(2**attempt)
    raise last or RuntimeError("request failed")

def period_key(s):
    s=str(s or ""); y=int(s[:4]) if s[:4].isdigit() else 0
    q=re.search(r"Q([1-4])",s.upper()); m=re.search(r"(?:M|-)(\d{1,2})",s)
    return (y,int(m.group(1)) if m else int(q.group(1))*3 if q else 12,s)

def age_days(period):
    try:
        y,m,*_=period_key(period); return max(0,(NOW_DT-datetime(y,m,1,tzinfo=timezone.utc)).days)
    except Exception:return None

def freshness(freq,period):
    age=age_days(period)
    if age is None:return "UNKNOWN"
    limits={"monthly":(75,180),"quarterly":(180,365),"semiannual":(300,600),"annual":(550,900)}
    f,s=limits.get(freq,(550,900))
    return "FRESH" if age<=f else "DELAYED" if age<=s else "STALE"

def stable_alert_id(cur,series,period,value):
    return hashlib.sha256(f"{cur}|{series}|{period}|{value}".encode()).hexdigest()[:20]

def normalize(rec,old=None):
    old=old or {}
    rec["previous_value"]=rec.pop("previous",rec.get("previous_value"))
    if rec.get("previous_value") not in (None,0):
        rec["change_pct"]=round((rec["value"]/rec["previous_value"]-1)*100,2)
    rec["engine_checked_at"]=NOW
    rec["freshness"]=freshness(rec.get("frequency"),rec.get("period"))
    rec.setdefault("official_publication_at",None)
    rec.setdefault("detection_lag_seconds",None)
    if rec["official_publication_at"] and rec.get("first_detected_at"):
        try:
            a=datetime.fromisoformat(rec["official_publication_at"].replace("Z","+00:00"))
            b=datetime.fromisoformat(rec["first_detected_at"].replace("Z","+00:00"))
            rec["detection_lag_seconds"]=max(0,int((b-a).total_seconds()))
        except Exception:pass
    return rec

def bls_us():
    series="CES0500000003"
    b,_=get("https://api.bls.gov/publicAPI/v2/timeseries/data/"+series,{"Accept":"application/json"},timeout=25)
    j=json.loads(b); rows=j.get("Results",{}).get("series",[{}])[0].get("data",[])
    obs=[]
    for r in rows:
        p=r.get("period","")
        if not p.startswith("M") or p=="M13":continue
        try:v=float(r["value"])
        except Exception:continue
        obs.append((f'{r["year"]}-{int(p[1:]):02d}',v))
    obs=sorted(obs,key=lambda x:period_key(x[0]))
    if len(obs)<2:raise RuntimeError("BLS returned fewer than 2 monthly observations")
    (p0,v0),(p1,v1)=obs[-2],obs[-1]
    return {"currency":"USD","value":v1,"previous_value":v0,"period":p1,"frequency":"monthly","unit":"USD/hour",
      "definition":"Average Hourly Earnings of All Employees, Total Private","series_id":series,"primary_source":"BLS",
      "source_quality":"NATIONAL_OFFICIAL","source_url":"https://api.bls.gov/publicAPI/v2/timeseries/data/"+series,
      "source_checked_at":NOW,"observation_verified_at":NOW}

def ilo_download(ind):
    q=urllib.parse.urlencode({"id":ind,"lang":"en","type":"code","format":".csv","channel":"ilostat"})
    b,h=get("https://rplumber.ilo.org/data/indicator/?"+q,{"Accept":"text/csv,*/*"},timeout=45,retries=3)
    t=b.decode("utf-8-sig","replace")
    if "ref_area" not in t[:5000].lower():raise RuntimeError("unexpected ILOSTAT response")
    return t,h

def parse_ilo(text,wanted):
    try:d=csv.Sniffer().sniff(text[:10000],delimiters=",;\t").delimiter
    except Exception:d=","
    out=[]
    for raw in csv.DictReader(io.StringIO(text),delimiter=d):
        r={str(k).strip().lower():(v or "").strip() for k,v in raw.items() if k}
        cur=AREA_TO_CUR.get(r.get("ref_area",""))
        if not cur or cur not in wanted:continue
        if r.get("sex","") not in ("","SEX_T","T","TOTAL"):continue
        cl=r.get("classif1","")
        if cl and cl not in ("ECO_ISIC4_TOTAL","ECO_ISIC3_TOTAL","TOTAL") and not cl.endswith("_TOTAL"):continue
        try:v=float(r.get("obs_value",r.get("value","")))
        except Exception:continue
        p=r.get("time",r.get("time_period",""))
        if p:out.append((cur,p,v))
    return out

PROBES={
"EUROSTAT":"https://ec.europa.eu/eurostat/api/dissemination/catalogue/rss/en/statistics-update.rss",
"OECD":"https://sdmx.oecd.org/public/rest/data/OECD.ELS.SAE,DSD_EARNINGS@AV_AN_WAGE,1.0/all?startPeriod=2024&dimensionAtObservation=AllDimensions",
"ONS_UK":"https://api.beta.ons.gov.uk/v1/datasets?limit=1",
"STATCAN_CA":"https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1410022201",
"ABS_AU":"https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/average-weekly-earnings-australia/latest-release",
"ESTAT_JP":"https://www.e-stat.go.jp/en/stat-search/database","KOSIS_KR":"https://kosis.kr/eng/","INEGI_MX":"https://www.inegi.org.mx/",
"GASTAT_SA":"https://www.stats.gov.sa/en","FSO_CH":"https://www.bfs.admin.ch/bfs/en/home/statistics/work-income/wages-income-employment-labour-costs.html",
"STATISTICS_PL":"https://stat.gov.pl/en/","SSB_NO":"https://www.ssb.no/en/arbeid-og-lonn/lonn-og-arbeidskraftkostnader",
"NBS_CN":"https://www.stats.gov.cn/english/","MOSPI_IN":"https://www.mospi.gov.in/"}

def probes():
    out={}
    for name,url in PROBES.items():
        try:
            b,h=get(url,timeout=12,retries=2);out[name]={"ok":True,"mode":"MONITOR","checked_at":NOW,"bytes":len(b),"last_modified":h.get("Last-Modified"),"url":url}
        except Exception as e:out[name]={"ok":False,"mode":"MONITOR","checked_at":NOW,"error":str(e)[:180],"url":url}
    return out

old=load(DATA/"salaries.json",{"records":[]}); oldmap={r.get("currency"):r for r in old.get("records",[]) if r.get("currency")}
records={}; health={"checked_at":NOW,"engine":"GSWL Data Engine V4.2","sources":{}}

# National adapter: US BLS. It is intentionally not numerically compared to ILOSTAT.
try:
    r=bls_us(); o=oldmap.get("USD",{})
    same=(o.get("primary_source")=="BLS" and o.get("series_id")==r["series_id"] and o.get("period")==r["period"] and o.get("value")==r["value"])
    r["first_detected_at"]=o.get("first_detected_at") if same else NOW
    r["last_alert_at"]=o.get("last_alert_at") if same else None
    records["USD"]=normalize(r,o)
    health["sources"]["BLS_US"]={"ok":True,"mode":"ACTIVE_FEED","checked_at":NOW,"series_id":r["series_id"],"latest_period":r["period"]}
except Exception as e:
    health["sources"]["BLS_US"]={"ok":False,"mode":"ACTIVE_FEED","checked_at":NOW,"error":str(e)[:180]}

# ILOSTAT fallback, with frequency priority and retry/backoff.
collected={}; freqmap={}
for ind,freq in ILO:
    try:
        txt,h=ilo_download(ind);health["sources"]["ILOSTAT_"+freq.upper()]={"ok":True,"mode":"ACTIVE_FALLBACK","checked_at":NOW,"last_modified":h.get("Last-Modified")}
        wanted=set(AREA_TO_CUR.values())-set(collected)
        for cur,p,v in parse_ilo(txt,wanted):collected.setdefault(cur,[]).append((p,v));freqmap[cur]=freq
    except Exception as e:health["sources"]["ILOSTAT_"+freq.upper()]={"ok":False,"mode":"ACTIVE_FALLBACK","checked_at":NOW,"error":str(e)[:180]}

for cur,obs in collected.items():
    if cur in records:continue
    bp={p:v for p,v in obs}; ordered=sorted(bp.items(),key=lambda x:period_key(x[0]))
    p,v=ordered[-1]; prev=ordered[-2] if len(ordered)>1 else (None,None); o=oldmap.get(cur,{})
    same=(o.get("primary_source")=="ILOSTAT" and o.get("period")==p and o.get("value")==v)
    r={"currency":cur,"value":v,"previous_value":prev[1],"period":p,"frequency":freqmap[cur],"unit":"local_currency",
       "definition":"Average earnings (ILOSTAT fallback)","series_id":"ILOSTAT:"+freqmap[cur],"primary_source":"ILOSTAT","source_quality":"FALLBACK",
       "source_url":"https://rplumber.ilo.org/data/indicator/","source_checked_at":NOW,"observation_verified_at":NOW,
       "first_detected_at":o.get("first_detected_at") if same else NOW,"last_alert_at":o.get("last_alert_at") if same else None}
    records[cur]=normalize(r,o)

# Preserve last official observation on source failure, but DO NOT pretend it was source-verified now.
for cur,o in oldmap.items():
    if cur in records:continue
    kept=dict(o);kept["engine_checked_at"]=NOW;kept["retained_previous_official"]=True
    kept["freshness"]=freshness(kept.get("frequency"),kept.get("period"))
    kept.setdefault("previous_value",kept.pop("previous",None))
    records[cur]=kept

health["sources"].update(probes())

# Idempotent alerts only when the exact series has a newly detected observation.
hist=load(DATA/"alerts.json",{"alerts":[]}); alerts=hist.get("alerts",[]); seen={a.get("alert_id") for a in alerts}
for cur,r in records.items():
    o=oldmap.get(cur,{})
    is_new=bool(o) and (o.get("series_id")==r.get("series_id")) and (o.get("period")!=r.get("period") or o.get("value")!=r.get("value"))
    ch=r.get("change_pct")
    if is_new and ch is not None and ch>5:
        aid=stable_alert_id(cur,r.get("series_id"),r.get("period"),r.get("value"))
        if aid not in seen:
            alerts.append({"alert_id":aid,"currency":cur,"series_id":r.get("series_id"),"period":r.get("period"),"change_pct":ch,"created_at":NOW,"source":r.get("primary_source")})
            seen.add(aid);r["last_alert_at"]=NOW

ordered=[records[c] for c in ALL if c in records]
if not ordered:sys.exit("ERROR: zero usable official salary records")
for r in ordered:
    if r.get("currency") not in ALL or r.get("value") is None or not r.get("period") or not r.get("primary_source"):
        sys.exit("ERROR: invalid normalized record: "+repr(r))

payload={"updated":NOW,"engine_checked_at":NOW,"records":ordered,"meta":{"engine":"GSWL Data Engine V4.2","record_count":len(ordered),
"target_zone_count":20,"unavailable_count":20-len(ordered),"check_frequency":"hourly","selection_policy":"national official adapter > compatible supranational adapter > ILOSTAT fallback > retained last official",
"field_semantics":{"engine_checked_at":"this workflow ran","source_checked_at":"the source answered","observation_verified_at":"this exact observation was seen from its source","first_detected_at":"first time GSWL saw this exact observation"},
"warning":"Values with different units/definitions are never compared across series."}}
atomic_json(DATA/"salaries.json",payload);atomic_json(DATA/"source_health.json",health);atomic_json(DATA/"alerts.json",{"updated":NOW,"alerts":alerts})
print(f"GSWL V4.2 OK: {len(ordered)}/20 stored; active BLS={health['sources'].get('BLS_US',{}).get('ok')}; alerts={len(alerts)}")
