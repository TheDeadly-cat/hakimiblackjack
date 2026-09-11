import sys,json,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.analysis.split_contracts import split_research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import AnalysisService

def main():
 output=ROOT/'.local-evidence/depleted-stress-20260911'
 output.mkdir(exist_ok=False)
 plan=[(n,kind) for n in (6,7,8) for kind in ('all-tens-removed','half-tens-removed','middle-ranks-removed')]
 (output/'frozen-cases.json').write_text(json.dumps(dict(cases=plan,player=['2','2'],dealer_up='A',budget_seconds=5,purpose='additional adversarial composition cohort; separate from the frozen 318-case matrix'),indent=2),encoding='utf-8')
 rows=[]
 for decks,kind in plan:
  l=EventLedger('synthetic-depleted-stress');l.start_session('Self-generated stress history, not a real table');l.create_shoe(split_research_rules(decks))
  rounds=decks*(2 if kind=='half-tens-removed' else 4)
  for _ in range(rounds):
   l.start_round(['玩家1'])
   cards=('5','6','8','9') if kind=='middle-ranks-removed' else ('10','J','Q','K')
   l.deal('庄家',cards[2]);l.deal('庄家',cards[3]);l.deal('玩家1',cards[0]);l.deal('玩家1',cards[1])
   l.end_round(settle=True,observation_status='complete')
  l.start_round(['玩家1']);l.deal('庄家','A');l.deal('庄家',hidden=True);l.deal('玩家1','2');l.deal('玩家1','2');l.peek_negative()
  start=time.perf_counter();s=build_input(l,'玩家1');input_seconds=time.perf_counter()-start
  service=AnalysisService();start=time.perf_counter()
  try:
   service.start(s)
   while True:
    r=service.poll()
    if r is not None:break
    time.sleep(.003)
   row=dict(decks=decks,kind=kind,status=r['status'],wall_seconds=time.perf_counter()-start,input_seconds=input_seconds,counts=s.counts,result=r)
   rows.append(row);(output/f'{decks}-{kind}.json').write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
   print(decks,kind,r['status'],round(row['wall_seconds'],3),flush=True)
  finally:service.close()
 (output/'receipt.json').write_text(json.dumps(dict(cases=rows,all_completed=all(r['status']=='available' for r in rows)),ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
