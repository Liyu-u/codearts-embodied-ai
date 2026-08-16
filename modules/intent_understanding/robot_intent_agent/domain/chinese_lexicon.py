"""Recall lexicon.  These aliases never decide a role or entity ID."""

from __future__ import annotations

ACTION_LEXICON = {
    "GRASP": ("抓", "拿", "取", "握", "夹"),
    "PLACE": ("放", "摆", "置", "装入"),
    "TRANSFER": ("上料", "搬运", "移", "转移", "送到", "运到"),
    "HANDOVER": ("递", "交给", "给我", "传给"),
    "WAIT": ("等", "等待", "直到"),
}

ROLE_LEXICON = {
    "theme": ("工件", "零件", "物体", "杯子", "瓶子", "目标"),
    "destination": ("到", "至", "放入", "放到", "检测区", "料箱", "托盘"),
    "source": ("从", "来自", "工位"),
    "recipient": ("给", "交给", "递给", "操作员", "用户", "我"),
    "obstacle": ("避开", "绕开", "不要碰", "夹具", "障碍物"),
}

SPATIAL_LEXICON = {
    "LEFTMOST": ("最左", "左边最靠边"), "RIGHTMOST": ("最右",),
    "LEFT": ("左边", "左侧"), "RIGHT": ("右边", "右侧"),
    "FRONT": ("前面", "前方"), "BEHIND": ("后面", "后方"),
    "NEAR": ("附近", "靠近", "旁边"), "MIDDLE": ("中间",),
}

NEGATION_LEXICON = ("不要", "别", "禁止", "避免", "不能", "不可", "不许")
