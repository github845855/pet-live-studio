"""弹幕指令：把一条短弹幕解析成「照料」或「指定动作」。

只在**整句恰好等于关键词**时命中（去掉标点和空白后比较），所以
「你会跳舞吗」不会被当成指令，真正的「跳舞」才会。指令不走模型、不出声，
舞台立刻播动作 + 冒一句话，延迟最低。
"""

import re

# 照料指令 → PetState 里的命令名
CARE_ALIASES = {
    "feed": ("喂食", "喂饭", "投喂", "投食", "开饭", "吃饭", "吃吧", "给你吃", "吃点东西"),
    "play": ("陪玩", "一起玩", "陪我玩", "玩球", "玩耍", "来玩"),
    "sleep": ("哄睡", "睡觉", "去睡觉", "睡吧", "晚安", "该睡了"),
    "pat": ("摸头", "摸摸", "顺毛", "抱抱", "贴贴", "撸撸", "揉揉"),
}

# 指定动作 → (片段名, 气泡)
ACTION_ALIASES = {
    "跳舞": ("优雅女仆舞", "好，跳一个！"),
    "转圈": ("小幅度原地360度旋转展示", "看好了，转一圈～"),
    "生气": ("傲娇生气", "哼，我生气了！"),
    "害羞": ("点击回应-害羞惊讶", "别、别这样啦……"),
    "笑一个": ("点击回应-挠痒咯咯笑", "哈哈哈，好痒！"),
    "打招呼": ("点击回应-元气挥手", "大家好呀！"),
    "挥手": ("点击回应-元气挥手", "拜拜～"),
    "伸懒腰": ("超大伸懒腰", "伸个大大的懒腰……"),
    "打个滚": ("弹飞", "哇——飞起来了！"),
    "跳一下": ("点击回应-开心跃动", "跳一个！"),
    "卖个萌": ("可爱宅舞", "给你卖个萌～"),
}

CLEANUP = re.compile(r"[\s\u3000，。！？!?~～、,.]+")

MAX_COMMAND_LENGTH = 8


def match(text):
    """返回 {'type': 'care', 'command': ...} 或 {'type': 'action', 'clip': ..., 'text': ...}。"""
    cleaned = CLEANUP.sub("", str(text or ""))
    if not cleaned or len(cleaned) > MAX_COMMAND_LENGTH:
        return None
    for command, words in CARE_ALIASES.items():
        if cleaned in words:
            return {"type": "care", "command": command}
    hit = ACTION_ALIASES.get(cleaned)
    if hit:
        return {"type": "action", "clip": hit[0], "text": hit[1]}
    return None
