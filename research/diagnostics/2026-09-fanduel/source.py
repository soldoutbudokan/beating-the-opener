"""Read-only quote/gate diagnostic; no outcome scoring or forecast rebuilding.

Reads exact committed bytes, using matching local files as a cache. Keeps
2025 quote reuse explicit; nothing here is an independent model evaluation.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import argparse, csv, gzip, hashlib, io, json, subprocess
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
ET = ZoneInfo('America/New_York')
MARKETS = (393,397,391,390,396,394,395,398,399,392,401,400)

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True,
                    help='Fresh JSON or .json.gz destination; never overwritten')
args = parser.parse_args()
OUT = args.output
if OUT.exists():
    parser.error(f'refusing to overwrite {OUT}')
SOURCE = 'd2b5e685ce10d9670fc93bf8c22810bc8cc5df83'
LIVE = {393,397,391,390,396,394,395,398}
CORE = {393,397,391,390}
KNOWN = ('PANEL_STALE','STALE_PLAYER','TEAM_MISMATCH','TEAM_CHANGED',
         'NO_OPEN_QUOTE','OPEN_INCOHERENT','NON_FD_OPEN','NO_FD_QUOTE',
         'MOVED_OFF_OPEN','SUSPECT_EV','PLAYER_ALREADY_BET')

source_tree = subprocess.check_output(
    ['git','ls-tree','-r','-z',SOURCE,'--','wnba/data/raw/bp','wnba/live/projections.csv'],cwd=ROOT)
source_blobs = {}
for line in source_tree.split(b'\0'):
    if line:
        metadata, path = line.split(b'\t',1)
        source_blobs[path.decode()] = metadata.split()[2].decode()
input_manifest = {}
input_cache = {}

def read_source(path):
    relative = str(path.relative_to(ROOT))
    if relative in input_cache:
        return input_cache[relative]
    blob = source_blobs[relative]
    data = path.read_bytes() if path.exists() else b''
    local_blob = hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()
    if local_blob != blob:
        data = subprocess.check_output(['git','cat-file','blob',blob],cwd=ROOT)
    input_manifest[relative] = {'git_blob':blob,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
    input_cache[relative] = data
    return data

def load(path):
    return json.loads(gzip.decompress(read_source(path)))

def parse(field, value):
    # Pinned BettingPros contract: naive scheduled/tip values explicitly mean UTC.
    if field != 'bp.scheduled' or not isinstance(value,str) or ('T' not in value and ' ' not in value):
        raise ValueError('an explicit BettingPros scheduled instant is required')
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    if result.utcoffset().total_seconds() != 0:
        raise ValueError('BettingPros scheduled timestamp must use UTC')
    return result.astimezone(timezone.utc)

def opening(offer):
    # Exactly the Phase 0 source-mix pairing rule; no outcome/availability filter.
    sides={s.get('selection'):s.get('opening_line') or {} for s in offer.get('selections',[])}
    over,under=sides.get('over',{}),sides.get('under',{})
    try:
        line,other=float(over['line']),float(under['line'])
        oc,uc=float(over['cost']),float(under['cost'])
        if abs(oc)<100 or abs(uc)<100:return None
        prob=lambda c:100/(100+c) if c>0 else -c/(100-c)
        if line!=other or over['book_id']!=under['book_id'] or not 1<=prob(oc)+prob(uc)<=1.15:return None
        return line
    except (ValueError,TypeError,KeyError):return None

def fd_current(offer):
    sides={}
    for selection in offer.get('selections',[]):
        for book in selection.get('books',[]):
            if book['id']==10:
                for line in book.get('lines',[]):
                    if line.get('main') and not line.get('is_off') and line.get('active'):
                        sides[selection['selection']]=line
    try:
        over,under=sides['over'],sides['under']
        oc,uc=float(over['cost']),float(under['cost'])
        prob=lambda c:100/(100+c) if c>0 else -c/(100-c)
        return float(over['line'])==float(under['line']) and min(abs(oc),abs(uc))>=100 and 1<=prob(oc)+prob(uc)<=1.15
    except (KeyError,ValueError,TypeError):return False

books={}; markets={}; daily=defaultdict(Counter); domains=Counter(); examples={}
for year in (2025,2026):
    cohorts={name:Counter() for name in ('all_12','live_8','core_4')}
    market_counts=defaultdict(Counter)
    seen=set()
    for event in load(ROOT / f'wnba/data/raw/bp/events_{year}.json.gz'):
        day=parse('bp.scheduled',event['scheduled']).astimezone(ET).date().isoformat()
        if int(event.get('season',0))!=year or (year==2026 and not '2026-08-01'<=day<='2026-08-31'):continue
        for market in MARKETS:
            path=ROOT / f"wnba/data/raw/bp/offers/{event['id']}_{market}.json.gz"
            if str(path.relative_to(ROOT)) not in source_blobs:continue
            data=load(path)
            for offer in data.get('offers',[]):
                if str(offer['event_id'])!=str(event['id']):continue
                key=(str(event['id']),str(offer.get('player_id')),int(offer['market_id']))
                if key in seen:continue
                seen.add(key)
                if opening(offer) is None:continue
                side=next(s for s in offer['selections'] if s['selection']=='over')
                book=side['opening_line']['book_id']
                current=fd_current(offer)
                mc=market_counts[str(market)];mc['total']+=1;mc[f'book_{book}']+=1
                if current:mc['current_fd_coherent']+=1
                for cohort,keep in (('all_12',True),('live_8',market in LIVE),('core_4',market in CORE)):
                    if keep:
                        c=cohorts[cohort]; c['total']+=1; c[f'book_{book}']+=1
                        if current:c['current_fd_coherent']+=1;c[f'current_fd_coherent_open_{book}']+=1
                if year==2026:
                    daily[day]['total']+=1;daily[day][f'book_{book}']+=1
                    for s in offer['selections']:
                        for b in s.get('books',[]):
                            if b['id']==14:
                                for line in b.get('lines',[]):
                                    link=line.get('link','');domain=urlsplit(link).hostname
                                    if domain:
                                        domains[domain]+=1
                                        examples.setdefault(domain,dict(path=str(path.relative_to(ROOT)),offer_id=offer.get('id'),book_id=14,link=link,source_sha256=hashlib.sha256(read_source(path)).hexdigest()))
    books[str(year)]=cohorts
    markets[str(year)]=market_counts

log=subprocess.check_output(['git','log',SOURCE,'--since=2026-07-31T00:00:00Z','--until=2026-09-01T00:00:00Z','--format=%H %aI','--','wnba/live/picks.csv'],cwd=ROOT,text=True)
commits=[line.split() for line in log.splitlines()]
payload=''.join(f'{sha}:wnba/live/picks.csv\n' for sha,when in commits).encode()
raw=subprocess.run(['git','cat-file','--batch'],cwd=ROOT,input=payload,stdout=subprocess.PIPE,check=True).stdout
buf=io.BytesIO(raw); rows=[]; snapshots=[]
for sha,when in commits:
    header=buf.readline().decode().strip().split(); size=int(header[2]); body=buf.read(size);buf.read(1)
    observed=datetime.fromisoformat(when).astimezone(timezone.utc)
    records=list(csv.DictReader(io.StringIO(body.decode())))
    snapshots.append(dict(commit=sha,observed_at=when,blob=header[0],sha256=hashlib.sha256(body).hexdigest(),rows=len(records)))
    for r in records:
        if not '2026-08-01'<=r.get('date','')<='2026-08-31':continue
        r=dict(r,commit=sha,observed_at=when)
        try:r['pregame']=observed<parse('bp.scheduled',r['tip'])
        except (ValueError,KeyError):r['pregame']=False
        r['blocking']=[f for f in KNOWN if f in r.get('flags','')]
        r['sole_non_fd']=r['blocking']==['NON_FD_OPEN'] and r.get('already_bet','').lower()!='true'
        r['playable']=r.get('play','').lower()=='true'
        rows.append(r)

def summary(values):
    c=Counter(); unique=defaultdict(set)
    for r in values:
        c['rows']+=1
        key=r['key'];pg=(r['event_id'],r['player']); unique['rows'].add(key);unique['player_games'].add(pg)
        for label,yes in [('non_fd','NON_FD_OPEN' in r['blocking']),('sole_non_fd',r['sole_non_fd']),('playable',r['playable'])]:
            if yes:c[label]+=1;unique[label].add(key);unique[label+'_player_games'].add(pg)
        for flag in r['blocking']:c[flag]+=1
        if r['sole_non_fd']:
            try:
                if float(r['fd_line'])==float(r['open_line']):c['sole_non_fd_same_original_line']+=1
            except (ValueError,KeyError):pass
    return {'row_counts':dict(c),'unique_counts':{k:len(v) for k,v in unique.items()},'sole_non_fd_player_games_never_seen_playable':len(unique['sole_non_fd_player_games']-unique['playable_player_games'])}

pregame=[r for r in rows if r['pregame']]
after_amendment=[r for r in pregame if datetime.fromisoformat(r['observed_at'])>=datetime(2026,8,8,22,50,42,tzinfo=timezone.utc)]
day_summaries={day:summary([r for r in after_amendment if r['date']==day]) for day in sorted({r['date'] for r in after_amendment})}
example_rows=[]
seen_examples=set()
for r in sorted(after_amendment,key=lambda r:r['observed_at']):
    if r['sole_non_fd'] and r['key'] not in seen_examples:
        example_rows.append({k:r.get(k) for k in ('commit','observed_at','key','date','player','market','side','fd_line','fd_cost','ev','open_line','flags','already_bet')})
        seen_examples.add(r['key'])
    if len(example_rows)==12:break

projections=list(csv.DictReader(io.StringIO(read_source(ROOT/'wnba/live/projections.csv').decode())))
pc=Counter();pgens=set();pm=defaultdict(Counter)
for r in projections:
    if not '2026-08-01'<=r.get('date','')<='2026-08-31':continue
    pc['rows']+=1;pgens.add(r['generated_utc'])
    pm[r['market']]['rows']+=1
    if r.get('ev_fd'):
        pc['nonmissing_ev_fd']+=1
        pm[r['market']]['nonmissing_ev_fd']+=1
        try:
            ev=float(r['ev_fd'])
            if ev>0.10:pc['ev_fd_gt_10pct']+=1
            if 0.10<ev<=0.25:pc['ev_fd_between_10_and_25pct']+=1
        except ValueError:pass

result={'schema':'fanduel-quote-gate-diagnostic-v1','evidence_kind':'reused-development-quotes-and-live-gate-records',
        'source_commit':SOURCE,'input_manifest':input_manifest,
        'source_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_book_counts':books,'market_counts':markets,'august_source_books_by_date':daily,
        'book14_link_domains':domains,'book14_examples':examples,'snapshot_count':len(snapshots),
        'snapshot_first_observed':min(s['observed_at'] for s in snapshots),'snapshot_last_observed':max(s['observed_at'] for s in snapshots),
        'all_saved_rows':summary(rows),'pregame_rows':summary(pregame),'post_amendment_pregame_rows':summary(after_amendment),
        'by_game_date_post_amendment':day_summaries,'sole_non_fd_examples':example_rows,
        'projections':{'counts':dict(pc),'distinct_generations':len(pgens),'by_market':pm},'snapshots':snapshots}
encoded=(json.dumps(result,indent=2,sort_keys=True)+'\n').encode()
if OUT.suffix=='.gz':encoded=gzip.compress(encoded,mtime=0)
OUT.parent.mkdir(parents=True,exist_ok=True)
with OUT.open('xb') as output:output.write(encoded)
print(json.dumps({'status':'complete','output':str(OUT),'sha256':hashlib.sha256(encoded).hexdigest(),
                  'input_files':len(input_manifest),'saved_snapshots':len(snapshots),
                  'protected_outcomes_scored':0},sort_keys=True))
