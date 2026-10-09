"""Rulebook help and a separate read-only insurance view. No bet transactions."""
from fractions import Fraction
import tkinter as tk
from tkinter import ttk

from .deal_entry import MODE_PEEK_WAIT
from ..analysis.split_contracts import BCLC_PROFILE

RULEBOOK_HELP = '''采用你提供的 LIVE BLACKJACK 手册1.1（2018-09-14）。普通七座；现行其他变体须单独核对。

主注：8副牌，庄家软17停牌，天然BJ净赔3:2。原始两张非21可加倍，之后只补一张。同点值可分一次，最多两手；两手先各补一张，再顺序行动。分牌后禁止加倍，不能再分；分A每手只补一张。分牌21按普通21结算。

检查BJ：仅A明牌时，在保险决定后检查。十点明牌不检查，仍有庄家BJ风险。请按实际检查记录“非BJ”，或录入实际揭示的底牌；底牌仅揭示原事件，不再扣一张牌。

追加注：本档按该手完整赌注结算庄家BJ。此项由手册每手完整赌注、分牌21不属BJ及BJ优先条款推导；若桌内帮助出现追加注退回条款，需要另建规则版本。

保险：庄家明牌为A时可选，最多原始主注的一半；净赔2:1，输则损失保险注。即使本人有BJ也可投保，保险与主注独立。这里仅展示保险研究值，主注EV和已结算记录不含保险注额。

边注：完美对子分完美／同色／混色对子；21+3分同花三条／同花顺／三条／顺子／同花。所给页缺少赔付表和A顺子细则，暂不填赔付。奖级取原始两张及庄家明牌，分牌不重复开奖。

跟注：跟随实际座位，不新增发牌座位。保险可独立选择；是否跟随加倍和分牌注额由跟注者选择。被跟随玩家未参与时原下注退回。软件可记录实际牌桌，暂不把跟注的个人注额并入主注结算。

网站操作：绿／黄开放下注，红色关闭；加倍下注按钮与牌局加倍不同。重复按钮沿用上一轮下注，撤销按钮只在下注开放时撤销网站下注。提前发牌只适用于唯一坐下且已下注的玩家。金色热玩家数字表示连续获胜轮数，输一轮即消失。以上是网站说明，本机按钮不发送下注或提前发牌。

本靴观察：烧牌数量、是否完整新靴、漏录和花色仍按实际记录。规则详情只改下个牌靴。精确主注模型目前要求完整新靴、零烧牌且无观察缺口；数量未知或非零烧牌均保留录牌并说明分析不可用。

手册未列正式半注投降，本档未开启；Cash Out不按固定半注投降处理。
'''


def insurance_summary(rules, plan, dealer_bj):
    if (rules is None or rules.profile_id != BCLC_PROFILE or rules.version != 2
            or plan is None or plan.mode != MODE_PEEK_WAIT or plan.dealer_up_rank != 'A'):
        return ''
    text = '保险独立于主注 · 最多半原注 · 净赔2:1'
    if dealer_bj.get('probability') is None:
        return text + ' · 组成待确认，研究EV不可用'
    # Use the already validated visible-prefix dealer probability. Per unit of
    # insurance, the two outcomes are +2 and -1; this is not a main-bet EV.
    p = Fraction(dealer_bj['fraction'])
    ev = 3 * p - 1
    return text + f' · 研究EV {float(ev):+.4f}/每1保险注'


def show_bclc_help(app):
    existing = getattr(app, 'bclc_help_window', None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        return
    win = app.bclc_help_window = tk.Toplevel(app)
    win.title('BCLC · 手册规则与本靴观察')
    win.geometry('740x620')
    body = ttk.Frame(win, padding=12)
    body.pack(fill=tk.BOTH, expand=True)
    text = tk.Text(body, wrap=tk.WORD, font=('Microsoft YaHei UI', 10))
    scroll = ttk.Scrollbar(body, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(fill=tk.BOTH, expand=True)
    text.insert('1.0', RULEBOOK_HELP)
    text.configure(state=tk.DISABLED)
    controls = ttk.Frame(win, padding=(12, 0, 12, 12))
    controls.pack(fill=tk.X)
    ttk.Button(controls, text='下个牌靴的规则与观察设置', command=app.act_rule_details).pack(side=tk.LEFT)
    ttk.Button(controls, text='关闭', command=win.destroy).pack(side=tk.RIGHT)
