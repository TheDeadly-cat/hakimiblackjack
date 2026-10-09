"""Independent local table presets. Selection never rewrites a locked shoe."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from ..analysis.split_contracts import BCLC_PROFILE, both_initial_das_rules
from ..core.rules import CONFIRM_UNKNOWN, CONFIRM_VERIFIED, RuleProfile
from ..storage.safe_files import atomic_write

PRAGMATIC = 'pragmatic'
BCLC = 'bclc'
MODE_LABELS = {PRAGMATIC: 'Pragmatic', BCLC: 'BCLC'}
SETTINGS_SCHEMA = 'hakimi-table-modes-v1'


def pragmatic_rules():
    """Preserve the user's previous preset, including its existing engine identity."""
    rules = both_initial_das_rules(8)
    rules.vendor = 'Pragmatic（本机常用设置）'
    rules.rule_source = '用户此前使用的本机设置：S17/仅A检查BJ/同值分牌先各补一张/晚投降/允许加倍；不是对所有Pragmatic桌的规则认证'
    return rules


def legacy_bclc_draft_rules():
    """Video-backed recording draft; unseen deck/peek/burn rules remain unconfirmed."""
    rules = both_initial_das_rules(8, surrender=None)
    rules.profile_id = BCLC_PROFILE
    rules.vendor = 'Evolution / PlayNow（BCLC）'
    rules.game_name = 'PlayNow Blackjack 3（普通七座）'
    rules.table_id = 'PlayNow Blackjack 3'
    rules.verify_date = '2026-10-07'
    rules.rule_source = ('PlayNow官方Live Blackjack说明及用户提供视频；可见3:2、庄家17停牌、'
                         '玩家从画面右向左、起手底牌、分牌两手先各补一张。'
                         '牌副数8、S17解释、DAS/最大分牌手数/分A/再分为待核对设置；'
                         'A/10检查、追加注结算、投降、烧牌与完整新靴未核实。')
    rules.confirm_status = CONFIRM_UNKNOWN
    rules.dealer_soft17 = None
    rules.double_after_split = None
    rules.resplit_aces = None
    rules.split_ace_hit_once = None
    rules.check_bj_when = None
    rules.dealer_bj_extra_bet_rule = None
    rules.burn_cards_known = None
    rules.initial_burn_count = None
    rules.start_from_new_shoe = None
    rules.remark = ('适用视频中的普通PlayNow Blackjack 3；Cash Out不是固定半注投降。'
                    'Speed和Infinite采用不同流程，不套用七座顺序。未核实项需在规则详情确认。')
    return rules


def bclc_rules():
    """Configured from the user's LIVE BLACKJACK manual v1.1, 2018-09-14.

    Table rules are distinct from observations of the shoe. This does not
    certify every current PlayNow variant or invent burn/start observations.
    """
    rules = legacy_bclc_draft_rules()
    rules.version = 2
    rules.game_name = 'PlayNow Live Blackjack（用户提供手册1.1）'
    rules.verify_date = '2026-10-09'
    rules.dealer_soft17 = 'S17'
    rules.double_after_split = False
    rules.resplit_aces = False
    rules.split_ace_hit_once = True
    rules.check_bj_when = 'before_player_actions_A'
    rules.dealer_bj_extra_bet_rule = 'all_bets_lost'
    rules.confirm_status = CONFIRM_VERIFIED
    rules.rule_source = ('用户提供的LIVE BLACKJACK手册图片1/2，版本1.1，2018-09-14；'
        '对应https://www.playnow.com/resources/documents/live-casino/how-to-play-blackjack.pdf；'
        '8副/S17/原手任意两张非21可加倍/同值仅分一次/两手先各补一张/无DAS/分A一张/仅A检查。'
        '庄家BJ按每手赌注输赢：分牌21不是BJ，追加注随该手计全额输；手册未列投降，本档未开启。'
        '此配置采用用户所给版本，不声称全部现行变体均相同；烧牌和整靴观察另行确认。')
    rules.remark = ('依据2018手册1.1，普通七座；仅A提供0.5原注以内的独立保险，赔付2:1。'
        'Cash Out不当作固定半注投降；Bet Behind跟随实际玩家，不增加实体座位。'
        '烧牌数量/从完整新靴开始以及边注赔付不得从手册缺项推断。')
    return rules


def upgraded_bclc_settings(settings):
    """Upgrade the next-shoe preset; never edit an already locked shoe."""
    if settings.rules.profile_id != BCLC_PROFILE or settings.rules.version >= 2:
        return copy.deepcopy(settings)
    updated = copy.deepcopy(settings)
    rules = bclc_rules()
    # Retain explicit user observations. Unknowns remain unknown.
    for field in ('burn_cards_known','initial_burn_count','start_from_new_shoe','cut_shuffle_note'):
        setattr(rules, field, getattr(settings.rules, field))
    updated.rules = rules
    return updated


def mode_for_rules(rules):
    if rules is not None and rules.profile_id == BCLC_PROFILE:
        return BCLC
    if rules is not None and 'Pragmatic' in (rules.vendor or ''):
        return PRAGMATIC
    # Legacy user preset retains its historical profile ID and vendor metadata.
    if rules is not None and rules.vendor == '本机常用设置':
        return PRAGMATIC
    return None


@dataclass
class ModeSettings:
    rules: RuleProfile
    deal_direction: str = 'forward'
    simple_hole: bool = True
    auto_next: bool = True

    def __post_init__(self):
        if self.deal_direction not in ('forward', 'reverse'):
            raise ValueError('桌面模式的发牌方向无效')
        if any(type(v) is not bool for v in (self.simple_hole, self.auto_next)):
            raise ValueError('桌面模式的开关必须为是或否')
        if not isinstance(self.rules, RuleProfile):
            raise ValueError('桌面模式缺少规则快照')

    def to_dict(self):
        return dict(rules=json.loads(self.rules.to_json()), deal_direction=self.deal_direction,
                    simple_hole=self.simple_hole, auto_next=self.auto_next)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {'rules', 'deal_direction', 'simple_hole', 'auto_next'}:
            raise ValueError('桌面模式设置字段不完整')
        return cls(RuleProfile(**data['rules']), data['deal_direction'], data['simple_hole'], data['auto_next'])


def default_settings(mode):
    if mode == PRAGMATIC:
        return ModeSettings(pragmatic_rules())
    if mode == BCLC:
        return ModeSettings(bclc_rules(), deal_direction='reverse', simple_hole=False)
    raise ValueError('未知桌面模式')


class TableModeStore:
    def __init__(self, db_path):
        self.path = Path(str(Path(db_path).resolve()) + '.table-modes.json')
        self._original = self.path.read_bytes() if self.path.exists() else None
        self.selected = PRAGMATIC
        self.modes = {}
        if self._original is not None:
            data = json.loads(self._original.decode('utf-8-sig'))
            if (data.get('schema') != SETTINGS_SCHEMA or data.get('selected') not in MODE_LABELS
                    or not isinstance(data.get('modes'), dict) or set(data['modes']) - set(MODE_LABELS)):
                raise ValueError('已保存的桌面模式格式无效；原文件保留')
            self.modes = {key: ModeSettings.from_dict(value) for key, value in data['modes'].items()}
            self.selected = data['selected']

    def get(self, mode):
        result = copy.deepcopy(self.modes.get(mode) or default_settings(mode))
        return upgraded_bclc_settings(result) if mode == BCLC else result

    def save_switch(self, previous, current_settings, selected, next_settings):
        if previous not in MODE_LABELS or selected not in MODE_LABELS:
            raise ValueError('未知桌面模式')
        current = self.path.read_bytes() if self.path.exists() else None
        if current != self._original:
            raise ValueError('桌面模式文件已被其他窗口更改；请重开本窗口后选择，原文件未覆盖')
        modes = copy.deepcopy(self.modes)
        modes[previous] = copy.deepcopy(current_settings)
        modes[selected] = copy.deepcopy(next_settings)
        old_bclc, new_bclc = self.modes.get(BCLC), modes.get(BCLC)
        if (self._original is not None and old_bclc and new_bclc
                and old_bclc.rules.version < 2 <= new_bclc.rules.version):
            # Preserve the entire original settings file, including byte order
            # and all Pragmatic preferences, before publishing the upgrade.
            original_hash = hashlib.sha256(self._original).hexdigest()
            backup = self.path.with_name(self.path.name + '.history') / (original_hash + '.json')
            try:
                atomic_write(backup, self._original, overwrite=False)
            except FileExistsError:
                if backup.read_bytes() != self._original:
                    raise ValueError('原模式设置备份不一致，未覆盖当前设置')
        payload = json.dumps({'schema': SETTINGS_SCHEMA, 'selected': selected,
                              'modes': {k: v.to_dict() for k, v in modes.items()}},
                             ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
        atomic_write(self.path, payload)
        self.modes, self.selected, self._original = modes, selected, payload
