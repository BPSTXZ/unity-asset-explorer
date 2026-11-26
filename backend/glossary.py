"""参数释义引擎：把 Unity 字段名/类名翻译成通俗中文，帮助理解脚本用处与参数含义。

三层来源：
1. Unity 引擎常见字段精确词典（exact=True）；
2. 游戏自定义字段按关键词推断（exact=False，标注“推测”）；
3. 类名按关键词 + 后缀推断脚本用处。
纯启发式，跨游戏通用；推断结果仅供参考。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 离线英汉词典包（ECDICT 常用词子集，约 4.4 万词）：游戏/科技关键词未命中时按词兜底翻译
try:
    ENDICT: dict[str, str] = json.loads(
        (Path(__file__).parent / "data" / "endict.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    ENDICT = {}

# Unity 引擎常见字段（归一化小写、去 m_ 前缀后匹配）
UNITY_FIELDS = {
    "name": "名称",
    "enabled": "启用（1=开，0=关）",
    "isactive": "是否激活",
    "gameobject": "所属游戏对象",
    "script": "脚本引用",
    "tagstring": "标签",
    "tag": "标签",
    "layer": "层级",
    "localposition": "本地位置",
    "position": "位置",
    "localrotation": "本地旋转",
    "rotation": "旋转",
    "localscale": "本地缩放",
    "scale": "缩放",
    "anchoredposition": "UI 锚点位置",
    "anchoredposition3d": "UI 三维锚点位置",
    "sizedelta": "UI 尺寸（宽×高）",
    "pivot": "UI 中心点",
    "anchormin": "UI 锚点最小",
    "anchormax": "UI 锚点最大",
    "color": "颜色",
    "sprite": "精灵图引用",
    "material": "材质引用",
    "mesh": "网格引用",
    "text": "文本内容",
    "fontsize": "字号",
    "font": "字体",
    "alignment": "对齐方式",
    "richtext": "富文本开关",
    "raycasttarget": "是否接收射线（点击）",
    "maskable": "是否受遮罩",
    "intensity": "强度",
    "range": "范围",
    "speed": "速度",
    "volume": "音量",
    "pitch": "音调",
    "loop": "循环播放",
    "playonawake": "启动时自动播放",
    "clip": "音频片段引用",
    "mute": "静音",
    "time": "时间",
    "duration": "时长",
    "value": "数值",
    "minvalue": "最小值",
    "maxvalue": "最大值",
    "wholenumbers": "仅整数",
    "interactable": "可交互",
    "targetgraphic": "目标图形",
    "onclick": "点击事件",
    "content": "内容",
    "horizontal": "水平",
    "vertical": "垂直",
    "spacing": "间距",
    "padding": "内边距",
    "cellsize": "单元格尺寸",
    "constraintcount": "约束数量",
    "startcolor": "起始颜色",
    "endcolor": "结束颜色",
    "startsize": "起始大小",
    "endsize": "结束大小",
    "startlifetime": "起始生命周期",
    "endlifetime": "结束生命周期",
    "emissionrate": "发射速率",
    "maxparticles": "最大粒子数",
    "gravitymodifier": "重力修正",
    "simulationspeed": "模拟速度",
    "simulationtime": "模拟时间",
    "normalizedtime": "归一化时间（0~1）",
    "weight": "权重",
    "radius": "半径",
    "height": "高度",
    "width": "宽度",
    "depth": "深度",
    "angle": "角度",
    "direction": "方向",
    "center": "中心",
    "size": "尺寸",
    "offset": "偏移",
    "alpha": "透明度",
    "fillamount": "填充量",
    "fillmethod": "填充方式",
    "preserveaspect": "保持宽高比",
    "pixelsperunit": "每单位像素数",
    "sortingorder": "排序顺序",
    "sortinglayer": "排序层",
    "receiveshadows": "接收阴影",
    "castshadows": "投射阴影",
    "sharedmaterial": "共享材质",
    "bones": "骨骼",
    "rootbone": "根骨骼",
    "updatewhenoffscreen": "离屏时仍更新",
    "fileid": "文件标识（引用）",
    "pathid": "路径标识（引用）",
}

# 颜色分量
COLOR_COMPONENTS = {"r": "红色分量", "g": "绿色分量", "b": "蓝色分量", "a": "透明度分量",
                    "x": "X 分量", "y": "Y 分量", "z": "Z 分量", "w": "W 分量"}

# 游戏自定义字段关键词（小写词 → 中文）
KEYWORDS = {
    "speed": "速度", "velocity": "速度", "accel": "加速度", "acceleration": "加速度",
    "health": "生命", "hp": "生命", "life": "生命", "lives": "生命数",
    "damage": "伤害", "attack": "攻击", "atk": "攻击", "power": "强度", "strength": "力量",
    "defense": "防御", "def": "防御", "armor": "护甲", "resist": "抗性",
    "price": "价格", "cost": "花费", "gold": "金币", "coin": "金币", "coins": "金币",
    "money": "货币", "currency": "货币", "cash": "现金", "gem": "宝石", "gems": "宝石",
    "count": "数量", "num": "数量", "number": "数量", "amount": "数量", "quantity": "数量",
    "max": "最大", "min": "最小", "limit": "上限", "cap": "上限",
    "time": "时间", "duration": "时长", "delay": "延迟", "interval": "间隔", "timeout": "超时",
    "rate": "比率", "chance": "概率", "probability": "概率", "prob": "概率", "odds": "几率",
    "level": "等级", "lv": "等级", "lvl": "等级", "exp": "经验", "experience": "经验",
    "score": "分数", "point": "分数", "points": "分数",
    "color": "颜色", "colour": "颜色", "size": "尺寸", "width": "宽", "height": "高",
    "scale": "缩放", "position": "位置", "pos": "位置", "offset": "偏移",
    "enabled": "启用", "active": "激活", "visible": "可见", "show": "显示", "hide": "隐藏",
    "text": "文本", "string": "字符串", "message": "消息", "msg": "消息",
    "name": "名称", "title": "标题", "id": "标识", "index": "索引", "key": "键",
    "target": "目标", "source": "来源", "dest": "目的地", "destination": "目的地",
    "start": "起始", "end": "结束", "begin": "开始", "finish": "完成",
    "length": "长度", "radius": "半径", "range": "范围", "distance": "距离",
    "angle": "角度", "rotation": "旋转", "rotate": "旋转", "force": "力",
    "multiplier": "倍率", "mult": "倍率", "factor": "系数", "ratio": "比例",
    "bonus": "加成", "reward": "奖励", "unlock": "解锁", "lock": "锁定", "locked": "锁定",
    "cooldown": "冷却", "cd": "冷却", "spawn": "生成", "respawn": "重生",
    "move": "移动", "movement": "移动", "jump": "跳跃", "anim": "动画", "animation": "动画",
    "sound": "音效", "audio": "音频", "sfx": "音效", "music": "音乐", "volume": "音量",
    "image": "图像", "sprite": "精灵图", "texture": "贴图", "icon": "图标", "photo": "图片",
    "dialog": "对话", "dialogue": "对话", "chat": "聊天", "speech": "语音",
    "quest": "任务", "mission": "任务", "task": "任务", "item": "物品", "inventory": "背包",
    "equip": "装备", "skill": "技能", "ability": "能力", "buff": "增益", "debuff": "减益",
    "enemy": "敌人", "monster": "怪物", "player": "玩家", "character": "角色", "hero": "英雄",
    "wave": "波次", "round": "回合", "stage": "关卡", "slot": "槽位", "grid": "网格",
    "puzzle": "拼图", "piece": "碎片", "tile": "格子", "cell": "单元格", "board": "棋盘",
    "shop": "商店", "buy": "购买", "sell": "出售", "offer": "优惠", "discount": "折扣",
    "timer": "计时", "counter": "计数", "progress": "进度", "percent": "百分比",
    "random": "随机", "seed": "随机种子", "weight": "权重", "priority": "优先级",
    "duration": "时长", "frequency": "频率", "threshold": "阈值", "tolerance": "容差",
    "input": "输入", "output": "输出", "result": "结果", "state": "状态", "status": "状态",
    "type": "类型", "kind": "种类", "mode": "模式", "flag": "标志", "option": "选项",
    "data": "数据", "config": "配置", "settings": "设置", "param": "参数", "params": "参数",
    "list": "列表", "array": "数组", "table": "表", "map": "映射", "pool": "池",
    "parent": "父级", "child": "子级", "children": "子级", "root": "根", "node": "节点",
    "left": "左", "right": "右", "top": "上", "bottom": "下", "up": "上", "down": "下",
    "horizontal": "水平", "vertical": "垂直", "center": "中心", "middle": "中间",
    "first": "首个", "last": "末尾", "next": "下一个", "prev": "上一个", "previous": "上一个",
    "current": "当前", "default": "默认", "total": "总计", "sum": "总和", "avg": "平均",
    "is": "是否", "has": "是否有", "can": "能否", "should": "应当", "use": "使用", "used": "已用",
    "on": "开启", "off": "关闭", "auto": "自动", "manual": "手动", "global": "全局", "local": "本地",
    "base": "基础", "extra": "额外", "additional": "附加", "initial": "初始", "final": "最终",
    "new": "新", "old": "旧", "temp": "临时", "tmp": "临时", "cache": "缓存", "buffer": "缓冲",
    "step": "步骤", "phase": "阶段", "tick": "节拍", "frame": "帧", "loop": "循环",
    "min": "最小", "max": "最大", "clamp": "钳制", "lerp": "插值", "smooth": "平滑",
    "spawn": "生成", "despawn": "消除", "create": "创建", "destroy": "销毁", "remove": "移除",
    "add": "增加", "subtract": "减少", "multiply": "乘", "divide": "除",
    "reward": "奖励", "penalty": "惩罚", "score": "得分", "combo": "连击", "streak": "连胜",
    "energy": "能量", "mana": "法力", "stamina": "耐力", "ammo": "弹药", "bullet": "子弹",
    "weapon": "武器", "gun": "枪", "sword": "剑", "shield": "盾",
    "boss": "首领", "minion": "小怪", "npc": "NPC", "avatar": "化身", "skin": "皮肤",
    "costume": "服装", "outfit": "套装", "hair": "头发", "face": "脸", "body": "身体",
    "emotion": "情绪", "mood": "心情", "expression": "表情", "pose": "姿势", "gesture": "手势",
    "scene": "场景", "world": "世界", "map": "地图", "zone": "区域", "area": "区域",
    "room": "房间", "door": "门", "chest": "宝箱", "treasure": "宝藏",
    "gift": "礼物", "daily": "每日", "weekly": "每周", "event": "活动", "season": "赛季",
    "pass": "通行证", "battle": "战斗", "war": "战争", "fight": "战斗", "combat": "战斗",
    "win": "胜利", "lose": "失败", "loss": "失败", "draw": "平局", "victory": "胜利",
    "rank": "排名", "rating": "评分", "star": "星级", "stars": "星级", "tier": "层级",
    "vip": "VIP", "premium": "高级", "free": "免费", "paid": "付费", "ad": "广告", "ads": "广告",
}

# 类名关键词
CLASS_WORDS = {
    "dialog": "对话框", "dialogue": "对话", "audio": "音频", "sound": "音效", "music": "音乐",
    "event": "活动", "photo": "图片", "image": "图像", "picture": "图片", "gallery": "画廊",
    "puzzle": "拼图", "player": "玩家", "enemy": "敌人", "item": "物品", "shop": "商店",
    "quest": "任务", "skill": "技能", "inventory": "背包", "camera": "相机", "ui": "界面",
    "button": "按钮", "window": "窗口", "menu": "菜单", "hud": "HUD", "game": "游戏",
    "level": "关卡", "score": "分数", "coin": "金币", "gold": "金币", "money": "货币",
    "reward": "奖励", "slot": "槽位", "farm": "农场", "battle": "战斗", "char": "角色",
    "character": "角色", "hero": "英雄", "anim": "动画", "animation": "动画", "particle": "粒子",
    "timer": "计时", "save": "存档", "config": "配置", "data": "数据", "load": "加载",
    "fullhd": "高清", "marathon": "马拉松活动", "odyssey": "奥德赛活动", "fakomat": "Fakomat",
    "prestige": "声望", "offer": "优惠", "special": "特殊", "daily": "每日", "calendar": "日历",
    "spinner": "转盘", "wheel": "转盘", "dice": "骰子", "card": "卡牌", "deck": "卡组",
    "grid": "网格", "board": "棋盘", "tile": "格子", "match": "匹配", "merge": "合并",
    "spawn": "生成", "pool": "池", "manager": "管理", "controller": "控制", "system": "系统",
    "helper": "辅助", "handler": "处理", "service": "服务", "provider": "提供", "factory": "工厂",
    "builder": "构建", "generator": "生成", "container": "容器", "view": "视图", "model": "模型",
    "component": "组件", "behaviour": "行为", "mono": "Mono", "script": "脚本", "object": "对象",
    "loader": "加载", "downloader": "下载", "uploader": "上传", "network": "网络", "http": "HTTP",
    "analytics": "统计", "track": "追踪", "log": "日志", "debug": "调试", "test": "测试",
    "input": "输入", "touch": "触摸", "gesture": "手势", "drag": "拖拽", "scroll": "滚动",
    "layout": "布局", "canvas": "画布", "render": "渲染", "shader": "着色器", "material": "材质",
    "texture": "贴图", "sprite": "精灵", "font": "字体", "text": "文本", "label": "标签",
    "progress": "进度", "bar": "条", "slider": "滑块", "toggle": "开关", "checkbox": "复选",
    "dropdown": "下拉", "list": "列表", "table": "表格", "tab": "标签页", "page": "页面",
    "popup": "弹窗", "toast": "提示", "notification": "通知", "alert": "警告", "confirm": "确认",
    "reward": "奖励", "chest": "宝箱", "gift": "礼物", "bonus": "加成", "boost": "加速",
    "booster": "道具", "power": "能量", "energy": "能量", "life": "生命", "health": "生命",
    "weapon": "武器", "bullet": "子弹", "projectile": "弹射物", "explosion": "爆炸", "effect": "特效",
    "vfx": "特效", "sfx": "音效", "voice": "语音", "narrator": "旁白", "subtitle": "字幕",
    "localization": "本地化", "language": "语言", "translate": "翻译", "i18n": "国际化",
    "settings": "设置", "options": "选项", "preferences": "偏好", "profile": "档案", "account": "账户",
    "login": "登录", "auth": "鉴权", "user": "用户", "friend": "好友", "social": "社交",
    "chat": "聊天", "message": "消息", "mail": "邮件", "inbox": "收件箱", "news": "新闻",
    "banner": "横幅", "icon": "图标", "avatar": "头像", "badge": "徽章", "medal": "勋章",
    "rank": "排名", "leaderboard": "排行榜", "achievement": "成就", "trophy": "奖杯", "stat": "统计",
    "stats": "统计", "counter": "计数", "tracker": "追踪", "monitor": "监控", "watcher": "监视",
    "scheduler": "调度", "queue": "队列", "stack": "栈", "buffer": "缓冲", "cache": "缓存",
    "session": "会话", "state": "状态", "machine": "机", "fsm": "状态机", "transition": "过渡",
    "tween": "补间", "ease": "缓动", "curve": "曲线", "path": "路径", "route": "路由",
    "navigation": "导航", "nav": "导航", "agent": "代理", "ai": "AI", "brain": "大脑",
    "decision": "决策", "behavior": "行为", "behaviour": "行为", "action": "动作", "command": "命令",
    "signal": "信号", "event": "事件", "listener": "监听", "observer": "观察者", "subscriber": "订阅",
    "publisher": "发布", "broker": "代理", "dispatcher": "分发", "emitter": "发射", "receiver": "接收",
    "location": "地点", "activity": "活动", "change": "切换", "small": "小型", "big": "大型", "custom": "自定义",
    "fx": "特效", "fly": "飞行", "fake": "虚假", "lean": "倾斜", "leave": "离开",
    "logout": "退出", "guest": "访客", "bio": "简介", "clicker": "点击器", "cheat": "作弊", "cheats": "作弊",
    "screen": "屏幕", "full": "全", "album": "相册", "splash": "闪屏", "seed": "种子",
    "tutorial": "教程", "loyalty": "忠诚度", "northern": "北方", "pass": "通行证", "season": "赛季",
    "multiply": "倍增", "start": "开始", "loop": "循环", "looped": "循环", "inapp": "应用内",
    "lootbox": "战利品箱", "loot": "战利品", "offline": "离线", "online": "在线", "internet": "网络",
    "wifi": "WiFi", "no": "无", "starter": "新手", "pack": "礼包", "bundle": "礼包",
    "friday": "星期五", "black": "黑色", "back": "返回", "as": "作为", "up": "提升",
    "cyber": "网络", "monday": "星期一", "halloween": "万圣节", "october": "十月", "title": "标题",
    "rewind": "回退", "serializable": "可序列化", "grow": "成长", "impulse": "冲动",
}

# 类名后缀（角色）
CLASS_SUFFIX = {
    "manager": "管理器", "controller": "控制器", "loader": "加载器", "component": "组件",
    "view": "视图", "window": "窗口", "item": "项", "config": "配置", "data": "数据",
    "system": "系统", "helper": "辅助工具", "handler": "处理器", "container": "容器",
    "generator": "生成器", "provider": "提供者", "service": "服务", "factory": "工厂",
    "builder": "构建器", "state": "状态", "info": "信息", "settings": "设置", "page": "页面",
    "popup": "弹窗", "panel": "面板", "widget": "控件", "element": "元素", "entry": "条目",
    "record": "记录", "row": "行", "cell": "单元格", "slot": "槽位", "group": "组",
    "collection": "集合", "list": "列表", "array": "数组", "map": "映射", "pool": "池",
    "queue": "队列", "stack": "栈", "cache": "缓存", "buffer": "缓冲", "tracker": "追踪器",
    "monitor": "监控器", "watcher": "监视器", "scheduler": "调度器", "dispatcher": "分发器",
    "emitter": "发射器", "receiver": "接收器", "listener": "监听器", "observer": "观察者",
    "agent": "代理", "driver": "驱动", "engine": "引擎", "core": "核心", "base": "基类",
    "abstract": "抽象", "interface": "接口", "impl": "实现", "wrapper": "包装", "adapter": "适配器",
    "proxy": "代理", "decorator": "装饰器", "strategy": "策略", "policy": "策略", "rule": "规则",
    "validator": "校验器", "checker": "检查器", "tester": "测试器", "mock": "模拟", "stub": "桩",
    "player": "播放器", "recorder": "录制器", "renderer": "渲染器", "painter": "绘制器",
    "drawer": "绘制器", "writer": "写入器", "reader": "读取器", "parser": "解析器",
    "serializer": "序列化器", "deserializer": "反序列化器", "encoder": "编码器", "decoder": "解码器",
    "converter": "转换器", "transformer": "变换器", "mapper": "映射器", "resolver": "解析器",
    "finder": "查找器", "searcher": "搜索器", "selector": "选择器", "picker": "选择器",
    "filter": "过滤器", "sorter": "排序器", "comparer": "比较器", "matcher": "匹配器",
    "spawner": "生成器", "creator": "创建器", "maker": "制造器", "producer": "生产者",
    "consumer": "消费者", "client": "客户端", "server": "服务端", "host": "主机", "peer": "对端",
    "connection": "连接", "socket": "套接字", "channel": "通道", "stream": "流", "pipe": "管道",
    "tube": "管道", "wire": "线路", "link": "链接", "reference": "引用", "ref": "引用",
    "pointer": "指针", "handle": "句柄", "token": "令牌", "ticket": "票据", "pass": "通行证",
    "key": "钥匙", "lock": "锁", "gate": "门", "barrier": "屏障", "wall": "墙", "floor": "地板",
    "ceiling": "天花板", "roof": "屋顶", "room": "房间", "house": "房屋", "building": "建筑",
    "city": "城市", "world": "世界", "planet": "星球", "star": "恒星", "sun": "太阳", "moon": "月亮",
}


# 科技 / Unity / 渲染 / 音频术语（优先于通用词典，避免 render→回报、channel→海峡 等误译）
TECH_WORDS = {
    "render": "渲染", "rendering": "渲染", "renderer": "渲染器", "channel": "通道", "channels": "通道",
    "preview": "预览", "default": "默认", "fx": "特效",
    "envelope": "包络", "bias": "偏置", "impulse": "脉冲", "mip": "多级渐远", "mipmap": "多级渐远贴图",
    "mipbias": "多级渐远偏置", "texture": "贴图", "texel": "纹素", "shader": "着色器", "mesh": "网格",
    "vertex": "顶点", "vertices": "顶点", "normal": "法线", "tangent": "切线", "binormal": "副法线",
    "uv": "UV坐标", "uv0": "UV0", "uv1": "UV1", "blend": "混合", "blending": "混合", "cull": "剔除",
    "culling": "剔除", "stencil": "模板", "buffer": "缓冲", "frame": "帧", "framerate": "帧率",
    "pixel": "像素", "voxel": "体素", "sprite": "精灵图", "atlas": "图集", "glyph": "字形",
    "kerning": "字距", "ligature": "连字", "descender": "下伸部", "ascender": "上伸部",
    "baseline": "基线", "hinting": "字形微调", "antialias": "抗锯齿", "antialiasing": "抗锯齿",
    "anisotropic": "各向异性", "bilinear": "双线性", "trilinear": "三线性", "depth": "深度",
    "quality": "质量", "forward": "前向", "deferred": "延迟", "shadow": "阴影", "shadows": "阴影",
    "lighting": "光照", "lightmap": "光照贴图", "reflection": "反射", "refraction": "折射",
    "ambient": "环境", "occlusion": "遮蔽", "bloom": "泛光", "flare": "光晕", "vignette": "暗角",
    "grain": "颗粒", "chromatic": "色差", "aberration": "像差", "distortion": "畸变",
    "saturation": "饱和度", "contrast": "对比度", "brightness": "亮度", "exposure": "曝光",
    "gamma": "伽马", "tone": "色调", "tonemapping": "色调映射", "mapping": "映射", "curve": "曲线",
    "decay": "衰减", "sustain": "延持", "release": "释放", "pitch": "音高",
    "pan": "声像", "reverb": "混响", "echo": "回声", "chorus": "合唱", "flanger": "镶边",
    "phaser": "相位", "compressor": "压缩器", "limiter": "限制器", "equalizer": "均衡器",
    "filter": "滤波器", "cutoff": "截止", "resonance": "共振", "sample": "采样", "samples": "采样",
    "sample rate": "采样率", "samplerate": "采样率", "frequency": "频率", "amplitude": "振幅",
    "waveform": "波形", "sine": "正弦", "square": "方波", "sawtooth": "锯齿", "triangle": "三角",
    "noise": "噪声", "white": "白", "pink": "粉红", "clip": "片段", "loop": "循环", "fade": "淡入淡出",
    "crossfade": "交叉淡化", "doppler": "多普勒", "rolloff": "衰减", "spatial": "空间",
    "spatialize": "空间化", "occlude": "遮挡", "obstruction": "障碍", "collision": "碰撞",
    "collider": "碰撞体", "rigidbody": "刚体", "trigger": "触发器", "raycast": "射线检测",
    "hit": "命中", "overlap": "重叠", "bounds": "边界", "bounding": "包围", "gizmo": "辅助图标",
    "pivot": "轴心", "anchor": "锚点", "rect": "矩形", "canvas": "画布", "viewport": "视口",
    "scroll": "滚动", "scrollbar": "滚动条", "slider": "滑块", "toggle": "开关", "dropdown": "下拉",
    "input": "输入", "output": "输出", "pointer": "指针", "cursor": "光标", "hover": "悬停",
    "press": "按下", "drag": "拖拽", "swipe": "滑动", "pinch": "捏合", "twist": "扭转",
    "gesture": "手势", "touch": "触摸", "click": "点击", "doubleclick": "双击", "focus": "焦点",
    "select": "选择", "selected": "已选", "highlight": "高亮", "navigation": "导航",
    "transition": "过渡", "animation": "动画", "animator": "动画控制器", "anim": "动画",
    "keyframe": "关键帧", "timeline": "时间轴", "duration": "时长", "delay": "延迟",
    "interval": "间隔", "elapsed": "已用时间", "remaining": "剩余", "countdown": "倒计时",
    "timer": "计时器", "stopwatch": "秒表", "timestamp": "时间戳", "delta": "增量", "deltatime": "帧间隔",
    "lerp": "插值", "slerp": "球面插值", "clamp": "钳制", "smooth": "平滑", "easing": "缓动",
    "ease": "缓动", "tween": "补间", "spring": "弹簧", "damping": "阻尼", "stiffness": "刚度",
    "velocity": "速度", "acceleration": "加速度", "deceleration": "减速度", "gravity": "重力",
    "mass": "质量", "drag": "阻力", "friction": "摩擦", "bounce": "弹跳", "bounciness": "弹性",
    "force": "力", "torque": "扭矩", "angular": "角", "rotation": "旋转", "rotate": "旋转",
    "scale": "缩放", "position": "位置", "local": "本地", "world": "世界", "space": "空间",
    "transform": "变换", "parent": "父级", "child": "子级", "root": "根", "bone": "骨骼",
    "skeleton": "骨架", "skin": "蒙皮", "skinned": "蒙皮", "weight": "权重", "weights": "权重",
    "blendshape": "融合变形", "morph": "变形", "target": "目标", "constraint": "约束",
    "ik": "反向动力学", "fk": "正向动力学", "retarget": "重定向", "avatar": "化身",
    "humanoid": "人形", "generic": "通用", "muscle": "肌肉", "pose": "姿势", "bind": "绑定",
    "bindpose": "绑定姿势", "inverse": "逆", "matrix": "矩阵", "quaternion": "四元数",
    "euler": "欧拉", "vector": "向量", "scalar": "标量", "normalize": "归一化", "magnitude": "模长",
    "direction": "方向", "distance": "距离", "radius": "半径", "height": "高度", "width": "宽度",
    "size": "尺寸", "offset": "偏移", "center": "中心", "extent": "范围", "min": "最小", "max": "最大",
    "range": "范围", "threshold": "阈值", "tolerance": "容差", "precision": "精度", "epsilon": "极小值",
    "random": "随机", "seed": "随机种子", "shuffle": "打乱", "permutation": "排列",
    "instance": "实例", "instantiate": "实例化", "prefab": "预制体", "clone": "克隆", "pool": "池",
    "spawn": "生成", "despawn": "消除", "destroy": "销毁", "create": "创建", "init": "初始化",
    "initialize": "初始化", "setup": "设置", "config": "配置", "configure": "配置", "settings": "设置",
    "option": "选项", "options": "选项", "preference": "偏好", "parameter": "参数", "param": "参数",
    "argument": "参数", "property": "属性", "field": "字段", "attribute": "特性", "component": "组件",
    "module": "模块", "system": "系统", "manager": "管理器", "controller": "控制器", "handler": "处理器",
    "listener": "监听器", "observer": "观察者", "event": "事件", "callback": "回调", "delegate": "委托",
    "signal": "信号", "message": "消息", "notification": "通知", "broadcast": "广播", "dispatch": "分发",
    "queue": "队列", "stack": "栈", "list": "列表", "array": "数组", "dict": "字典", "map": "映射",
    "set": "集合", "collection": "集合", "enumerable": "可枚举", "iterator": "迭代器", "index": "索引",
    "key": "键", "value": "值", "pair": "键值对", "entry": "条目", "item": "项", "element": "元素",
    "node": "节点", "tree": "树", "graph": "图", "path": "路径", "route": "路由", "link": "链接",
    "reference": "引用", "ref": "引用", "pointer": "指针", "handle": "句柄", "id": "标识", "guid": "全局标识",
    "hash": "哈希", "hashcode": "哈希码", "checksum": "校验和", "crc": "循环冗余校验",
    "serialize": "序列化", "deserialize": "反序列化", "encode": "编码", "decode": "解码",
    "encrypt": "加密", "decrypt": "解密", "compress": "压缩", "decompress": "解压缩",
    "parse": "解析", "format": "格式", "validate": "校验", "verify": "验证", "check": "检查",
    "test": "测试", "debug": "调试", "log": "日志", "trace": "追踪", "warn": "警告", "error": "错误",
    "exception": "异常", "assert": "断言", "breakpoint": "断点", "profiler": "性能分析",
    "benchmark": "基准测试", "performance": "性能", "optimize": "优化", "cache": "缓存",
    "lazy": "延迟", "preload": "预加载", "load": "加载", "unload": "卸载", "reload": "重载",
    "refresh": "刷新", "update": "更新", "upgrade": "升级", "patch": "补丁", "version": "版本",
    "build": "构建", "compile": "编译", "deploy": "部署", "release": "发布", "bundle": "资源包",
    "asset": "资源", "assets": "资源", "resource": "资源", "resources": "资源", "dependency": "依赖",
    "import": "导入", "export": "导出", "save": "保存", "load": "加载", "backup": "备份",
    "restore": "恢复", "reset": "重置", "clear": "清空", "flush": "刷新", "commit": "提交",
    "apply": "应用", "revert": "还原", "undo": "撤销", "redo": "重做", "sync": "同步", "async": "异步",
    "await": "等待", "task": "任务", "thread": "线程", "process": "进程", "job": "作业", "worker": "工作线程",
    "parallel": "并行", "concurrent": "并发", "lock": "锁", "mutex": "互斥锁", "semaphore": "信号量",
    "timeout": "超时", "retry": "重试", "attempt": "尝试", "fail": "失败", "success": "成功",
    "pending": "待处理", "running": "运行中", "paused": "已暂停", "stopped": "已停止", "done": "完成",
    "ready": "就绪", "idle": "空闲", "busy": "忙碌", "active": "激活", "inactive": "未激活",
    "enable": "启用", "disable": "禁用", "enabled": "启用", "disabled": "禁用", "visible": "可见",
    "invisible": "不可见", "hidden": "隐藏", "show": "显示", "hide": "隐藏", "open": "打开", "close": "关闭",
    "expand": "展开", "collapse": "折叠", "lock": "锁定", "unlock": "解锁", "locked": "锁定", "unlocked": "已解锁",
    "part": "部件", "parts": "部件", "piece": "碎片", "segment": "段", "section": "区段", "block": "块",
    "chunk": "块", "tile": "图块", "cell": "单元格", "grid": "网格", "board": "棋盘", "slot": "槽位",
    "guard": "守卫", "booster": "助推道具", "prestige": "声望", "affinity": "好感度", "vitraj": "彩窗",
    "material": "材质", "materials": "材质", "hold": "保持", "forever": "永久", "custom": "自定义",
    "shape": "形状", "source": "来源", "definition": "定义", "time": "时间", "global": "全局",
    "drop": "掉落", "drops": "掉落", "collection": "收集", "collect": "收集", "viz": "可视化",
    "visual": "视觉", "grass": "草", "ore": "矿石", "timber": "木材", "def": "定义", "spawn": "生成",
    "pickup": "拾取", "loot": "战利品", "node": "节点", "work": "工作", "behavior": "行为",
    "pair": "配对", "npc": "NPC", "creature": "生物", "puppet": "傀儡", "interactor": "交互器",
}


def _words(name: str) -> list[str]:
    n = re.sub(r"^m_", "", name)
    n = n.lstrip("_")
    n = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", n)
    n = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", n)
    return [p.lower() for p in re.split(r"[_\s]+", n) if p]


def _leaf(path: str) -> str:
    toks = re.findall(r"[^.\[\]]+", path)
    return toks[-1] if toks else path


def _lookup(w: str, table: dict) -> str:
    """精确命中优先，其次尝试常见复数还原（photos→photo、abilities→ability）。"""
    if w in table:
        return table[w]
    if len(w) > 3 and w.endswith("ies"):
        s = w[:-3] + "y"
        if s in table:
            return table[s]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        s = w[:-1]
        if s in table:
            return table[s]
    if len(w) > 3 and w.endswith("es"):
        s = w[:-2]
        if s in table:
            return table[s]
    return ""


def explain_field(path: str, ftype: str = "") -> dict:
    """返回 {name, zh, exact}：字段名、中文释义、是否精确。"""
    leaf = _leaf(path)
    low = re.sub(r"^m_", "", leaf.lower()).lstrip("_")
    if low in UNITY_FIELDS:
        return {"name": leaf, "zh": UNITY_FIELDS[low], "exact": True}
    if low in COLOR_COMPONENTS:
        return {"name": leaf, "zh": COLOR_COMPONENTS[low], "exact": True}
    words = _words(leaf)
    # 音频/视频 + player → 播放器
    parts = []
    for w in words:
        if w == "player" and any(x in words for x in ("audio", "sound", "music", "video", "anim", "animation")):
            parts.append("播放器")
        else:
            zh = _lookup(w, KEYWORDS) or _lookup(w, TECH_WORDS) or _lookup(w, ENDICT)
            if zh:
                parts.append(zh)
    if parts:
        return {"name": leaf, "zh": "".join(parts), "exact": False}
    return {"name": leaf, "zh": "", "exact": False}


def explain_class(cls: str) -> str:
    if not cls:
        return ""
    words = _words(cls)
    suffix_word = next((w for w in reversed(words) if w in CLASS_SUFFIX), "")
    suffix = CLASS_SUFFIX.get(suffix_word, "")
    parts = []
    for w in words:
        if w == suffix_word:
            continue
        if w == "player" and any(x in words for x in ("audio", "sound", "music", "video", "anim", "animation")):
            parts.append("播放器")
        else:
            zh = _lookup(w, CLASS_WORDS) or _lookup(w, TECH_WORDS) or _lookup(w, ENDICT)
            if zh:
                parts.append(zh)
    desc = "".join(parts)
    if suffix and suffix not in desc:
        desc += suffix
    return desc


# ---------------- 关键参数分类（游戏性强的词，避免通用 UI/引擎词） ----------------

CATEGORY_ZH = {
    "physics_move": "物理 & 移动",
    "survival": "生存",
    "combat": "战斗属性",
    "state_flag": "状态标记",
    "inventory": "背包 & 资源",
    "progress": "进度",
    "story": "剧情变量",
}
# 消歧优先级：越靠前越具体
CATEGORY_PRIORITY = ["story", "combat", "survival", "physics_move", "inventory", "progress", "state_flag"]

CATEGORY_WORDS = {
    "physics_move": set("""speed velocity accel acceleration decel deceleration jump force gravity mass drag
        friction bounce bounciness move movement walk run sprint dash fly flight swim climb rotate rotation
        angular turn steer steering thrust boost impulse momentum inertia rigidbody collider grounded airborne
        slope slide teleport warp hover fall falling landing launch trajectory physics kinematic knockback
        recoil movespeed walkspeed runspeed jumpforce jumpheight turnspeed gravityscale fallspeed maxspeed
        aircontrol dashforce boostforce movementspeed rotationspeed angularspeed maxvelocity terminalvelocity
        stepoffset skinwidth slopeangle maxslope dashspeed sprintspeed swimspeed flyspeed climbspeed""".split()),
    "survival": set("""health hp life lives vitality stamina energy mana oxygen breath hunger thirst defense
        def armor resist resistance toughness resilience regen regeneration regenerate heal healing lifesteal
        vampirism absorb absorption immunity immune invulnerable invincibility mortal death die dead alive
        respawn revive resurrection bleed bleeding poison poisoned burn burning chill chilled shock shocked
        curse cursed disease sick illness wound injury trauma fatigue exhaustion sanity madness corruption
        toxicity radiation maxhealth currenthealth healthregen damageresist damagereduction tenacity healamount
        healrate regenrate healthpersecond liferegen manaregen energyregen staminaregen armorpen magicresist
        physicalresist elementalresist blockchance dodgechance parrychance critresist""".split()),
    "combat": set("""attack atk damage dmg power strength weapon gun sword blade shield ammo ammunition bullet
        projectile fire shoot reload crit critical hit strike block parry dodge evade combat battle fight enemy
        boss minion mob aggro threat cooldown cd skill ability buff debuff spell cast accuracy spread penetration
        pierce piercing explosive blast dps attackspeed attackdamage attackpower critchance critdamage
        critmultiplier firerate reloadtime magazine clip shots stun stagger summon pet companion familiar golem
        elemental undead demon dragon beast creature monster target targeting aim aiming lockon homing hitscan
        hitbox hurtbox iframe superarmor hyperarmor poise interrupt channel channeling casting gcd charges charge
        charged finisher execute execution backstab ambush headshot weakpoint slashing bludgeoning magical
        physical true pure chaos slash pierce blunt attacks weaponrange attackrange combopoints rage fury berserk
        enrage enraged bash slam cleave whirlwind fireball frostbolt lightning meteor blizzard nova detonate
        ignite combustion dot hot aoe splash multishot rapidfire burst sniper rifle pistol shotgun cannon mortar
        grenade mine trap turret skillpower spellpower attackrange casttime castspeed""".split()),
    "state_flag": set("""locked unlock unlocked lock active inactive visible invisible hidden hide show frozen
        freeze paused pause mute muted silent invincible godmode stealth cloaked stunned sleep awake disabled
        selected highlighted focused hovered pressed toggled checked expanded collapsed opened closed shown
        displayed rendered drawn updated dirty changed modified saved loaded initialized started stopped running
        playing finished done ready busy idle waiting pending failed success error valid invalid available
        unavailable accessible permitted allowed denied granted revoked acquired lost found missing present
        absent existing null empty full incomplete partial equipped unequipped worn held carried stored deposited
        withdrawn transferred dropped discarded consumed used activated applied discovered visited cleared
        beaten completed unlocked purchased bought sold traded crafted forged upgraded enhanced refined""".split()),
    "inventory": set("""item items inventory equip equipment gear slot slots bag backpack pouch chest treasure
        gold coin coins money currency cash gem gems diamond diamonds jewel jewels loot drop pickup collect stack
        capacity carry hold own owned possession resource resources material materials ingredient ingredients
        craft crafting recipe forge upgrade enhance refine price cost buy sell shop store purchase vendor trade
        exchange consumable potion food drink elixir rune runes artifact relic shard shards fragment
        fragments essence dust ore ingot herb blueprint schematic token tokens
        ticket tickets coupon voucher gift gifts reward rewards bonus booster boosters powerup powerups
        collectible collectibles stash storage warehouse depot crate bundle outfit
        costume skin skins cosmetic cosmetics appearance wardrobe inventorysize maxslots slotcount itemcount
        stacksize maxstack carryweight weightlimit encumbrance pickuprange autoloot autopickup dropchance droprate
        rewardpool gacha pull reroll pity guarantee banner wishlist favorite""".split()),
    "progress": set("""level lv lvl exp experience xp score progress percent percentage stage chapter
        episode wave tier rank rating star stars prestige rebirth ascension milestone achievement
        achievements trophy trophies quest quests mission missions task tasks objective objectives goal goals
        complete completed completion discovered visited cleared beaten win victory highscore
        streak combo skillpoint talent perk renown fame glory honor badge badges medal medals challenge challenges
        bounty contract expedition campaign dungeon raid floor depth traveled explored revealed checklist todo
        progressbar completionrate questlog questline mainquest sidequest dailyquest weeklyquest
        queststage queststep questprogress missionstatus objectivecomplete levelup levelrequirement exprequirement
        exptolevel nextlevel maxlevel currentlevel playerlevel characterlevel accountlevel battlepasslevel
        starcount scoremultiplier pointsmultiplier leaderboard ranking ranked division league checkpoint
        savepoint respawnpoint autosave playthrough attempt tries attempts deaths kills killcount enemykills
        bosskills headshots shotsfired shotshit damagedealt damagetaken healingdone distancetraveled timeplayed
        playtime sessiontime gametime countdown timeleft timelimit""".split()),
    "story": set("""dialog dialogue dialogues conversation conversations talk talks speech speeches narrative
        narration story stories plot plots lore journal journals diary diaries note notes letter letters mail
        message messages msg subtitle subtitles caption captions voice voices narrator npc npcs relationship
        relationships affinity affection love romance trust friendship reputation karma morality moral choice
        choices decision decisions branch branches route routes ending endings cutscene cutscenes cinematic
        cinematics arc saga tale tales legend legends chronicle backstory flashback memory memories vision prophecy
        destiny fate dialogueid talkid conversationid storyid plotflag narrativeflag dialoguetree ink yarn twine
        renpy rpgmaker dialoguenode storynode branchnode choicenode endingnode dialogueline storyline plotline
        voiceline dialogtext storytext narrativetext dialogcontent storycontent dialogdata storydata dialogconfig
        storyconfig dialogvariable storyvariable dialogflag storyflag dialogswitch storyswitch dialogcondition
        storycondition dialogtrigger storytrigger dialogevent storyevent dialogsequence storysequence dialogbranch
        storybranch dialogchoice storychoice dialogending storyending dialogroute storyroute dialogpath storypath
        dialogflow storyflow dialoggraph storygraph dialogscript storyscript dialogmanager storymanager
        dialogcontroller storycontroller dialogsystem storysystem narrativedirector conversationmanager""".split()),
}

# 引擎基础字段 / 引用分量：始终排除（每个对象都有，属管线而非游戏参数）
BASE_BLOCK = {"gameobject", "enabled", "script", "name", "fileid", "pathid"}
# 管线叶子（哈希/GUID/实例ID/UnityEvent 回调等，非游戏参数）
PLUMB_SUBSTR = ("hashcode", "guid", "instanceid", "assemblytypename", "methodname", "callstate")

# 引擎 / UI / 渲染组件类名：整类跳过（其字段属界面管线，非游戏玩法参数）
ENGINE_CLASS_BLOCK = {
    # Unity UI
    "Image", "RawImage", "Text", "Graphic", "Mask", "RectMask2D", "CanvasGroup", "CanvasScaler",
    "GraphicRaycaster", "AspectRatioFitter", "ContentSizeFitter", "LayoutElement",
    "HorizontalLayoutGroup", "VerticalLayoutGroup", "GridLayoutGroup", "ScrollRect", "Scrollbar",
    "Slider", "Toggle", "Button", "Dropdown", "InputField", "Selectable", "EventTrigger",
    "HorizontalOrVerticalLayoutGroup", "LayoutGroup", "BaseMeshEffect", "Shadow", "Outline",
    "PositionAsUV1", "CanvasRenderer", "RectTransform", "Canvas",
    # TextMeshPro
    "TextMeshProUGUI", "TMP_Text", "TMP_SubMeshUI", "TMP_InputField", "TMP_FontAsset",
    "TMP_SpriteAsset", "TMP_Dropdown", "TMP_Scrollbar", "TMP_SelectionCaret", "MaterialReference",
    # Spine 渲染
    "SkeletonMecanim", "SkeletonGraphic", "SpineSkeletonGraphic", "SkeletonDataAsset",
    "SpineAtlasAsset", "SkeletonRenderer", "SkeletonAnimation", "BoneFollower", "PointFollower",
    "SlotFollower", "SkeletonUtility", "BoundingBoxFollower",
    # 常见 UI 资源 / 适配
    "SoftMask", "SoftMaskable", "NonDrawGraphic", "UIParticle", "EasyLayout", "ScaleFitter",
    "SafeAreaApplier", "ImageAdvanced", "ZoomableImage", "FullScreenImage", "ResizeListener",
    "SetCanvasBounds", "AspectRatioEnablerForPlatform", "GameGraphicRaycaster", "CustomSlider",
    "UIRoot", "UIGrid", "UITable", "UIPanel", "UIWidget", "UISprite", "UILabel", "UITexture",
    "UIAnchor", "UIStretch", "UISlicedSprite", "UITiledSprite", "UIFilledSprite",
    # 输入 / 事件系统
    "EventSystem", "StandaloneInputModule", "TouchInputModule", "BaseInputModule", "PointerInputModule",
    "PhysicsRaycaster", "Physics2DRaycaster", "LeanTouch", "LeanSelectable", "LeanGraphic", "LeanDrag",
    "LeanSwipe", "LeanTwist", "LeanPinch", "LeanScreenQuery", "LeanFinger", "LeanCommon", "LeanPlane",
    # 布料 / 物理模拟内部数据（逐顶点系数，非可调玩法参数）
    "ClothData", "Cloth", "SkinnedCloth",
    # Final IK / 骨骼求解
    "BipedIK", "LookAtIK", "GrounderBipedIK", "GrounderFBBIK", "GrounderQuadruped", "FullBodyBipedIK",
    "AimIK", "CCDIK", "FABRIK", "FABRIKRoot", "LimbIK", "TrigonometricIK", "GenericIK", "VRIK", "VRIKRoot",
    "RotationLimit", "RotationLimitAngle", "RotationLimitHinge", "RotationLimitSpline", "AimController",
    "HitReaction", "HitReactionVRIK", "Inertia", "OffsetModifier", "OffsetPose", "PelvisOffset",
    "Amplifier", "Twirl", "FingerBender", "HandPoser", "GenericPoser", "Poser", "Pose",
    # HDRP / URP / 渲染
    "HDAdditionalCameraData", "HDAdditionalLightData", "HDAdditionalReflectionData", "HDAdditionalSkyData",
    "HDAdditionalEnvData", "HDProbe", "PlanarReflectionProbe", "ReflectionProbe", "HDSceneSettings",
    "SkySettings", "HDRPSkySettings", "PhysicallyBasedSky", "GradientSky", "Volume", "VolumeProfile",
    "Bloom", "Vignette", "DepthOfField", "MotionBlur", "ColorAdjustments", "Tonemapping", "WhiteBalance",
    "LiftGammaGain", "ShadowsMidtonesHighlights", "SplitToning", "ColorCurves", "ChannelMixer",
    "FilmGrain", "LensDistortion", "ChromaticAberration", "PaniniProjection", "ScreenSpaceAmbientOcclusion",
    "ScreenSpaceReflections", "Exposure", "Fog", "AmbientOcclusion", "IndirectLightingController",
    "ContactShadows", "SubsurfaceScattering", "Transmission", "Refraction", "Compositor", "ColorLookup",
    "TextMeshPro", "TMP_SpriteAnimator", "VertexColor", "UiParticles",
    # Timeline / Playables
    "TimelineAsset", "AnimationTrack", "AudioTrack", "ControlTrack", "ActivationTrack", "PlayableTrack",
    "GroupTrack", "SignalTrack", "MarkerTrack", "AnimationPlayableAsset", "ControlPlayableAsset",
    "ActivationPlayableAsset", "AudioPlayableAsset", "PlayableMachine", "PlayableDirector", "TimelineClip",
}

# 引擎类名前缀（整族跳过：Cinemachine 相机、Magica 布料、HDRP 附加数据、Timeline 轨道等）
ENGINE_CLASS_PREFIXES = ("Cinemachine", "Magica", "HDAdditional", "UnityEngine.Rendering", "TMPro.",
                         "TLP_", "WaterSystem", "LensFlare", "DynamicBone", "VRMSpring", "UniVRM",
                         "KWS_", "HDRenderPipeline", "CustomPass", "ShorelineWaves", "WaterSurface")
# 引擎类名后缀（Timeline 轨道/片段/Playable 资产）
ENGINE_CLASS_SUFFIXES = ("Track", "Clip", "PlayableAsset", "Marker")


def is_engine_class(cls: str) -> bool:
    return (cls in ENGINE_CLASS_BLOCK or cls.startswith(ENGINE_CLASS_PREFIXES)
            or cls.endswith(ENGINE_CLASS_SUFFIXES))


def _is_plumbing(low: str) -> bool:
    return low in BASE_BLOCK or any(s in low for s in PLUMB_SUBSTR)


def _in(w: str, s: set) -> bool:
    """词命中（含常见复数还原：skills→skill、abilities→ability）。"""
    if w in s:
        return True
    if len(w) > 3 and w.endswith("ies") and (w[:-3] + "y") in s:
        return True
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss") and w[:-1] in s:
        return True
    if len(w) > 3 and w.endswith("es") and w[:-2] in s:
        return True
    return False


def categorize_field(path: str, ftype: str = "") -> str | None:
    """返回关键参数类别 key；非游戏关键参数返回 None。"""
    leaf = _leaf(path)
    low = re.sub(r"^m_", "", leaf.lower()).lstrip("_")
    if _is_plumbing(low):
        return None
    words = _words(leaf)
    matched = {cat for w in words for cat in CATEGORY_PRIORITY if _in(w, CATEGORY_WORDS[cat])}
    if matched:
        return next(c for c in CATEGORY_PRIORITY if c in matched)
    # 自定义布尔标记兜底归入状态标记（排除已知引擎字段）
    if ftype == "bool" and low not in UNITY_FIELDS and low not in COLOR_COMPONENTS:
        return "state_flag"
    return None


# ---------------- 重点关注概念（用户特别关注，优先聚合展示） ----------------
# 顺序即优先级：越靠前越具体
PRIORITY_CONCEPTS = [
    ("技能冷却", set("""cooldown cd cooltime recharge recast gcd globalcooldown cdr haste cooldownleft
        cooldownremaining cooldownrate cooldownreduction skillcooldown abilitycooldown spellcooldown
        cooldowntime cdtime recasttime rechargetime""".split())),
    ("已解锁技能", set("""skill skills ability abilities spell spells talent talents perk perks skilltree
        talenttree skilllist abilitylist learned mastered skillunlocked unlockedskills skilllevel skillrank
        abilitylevel spelllevel skillupgrade abilityupgrade""".split())),
    ("对话进度", set("""dialog dialogue dialogues conversation conversations talk talks speech narrative
        narration story plot dialogueid dialogueindex dialogueprogress dialoguestage conversationid talkid
        storyprogress plotflag narrativeflag dialoguenode branch choice ending cutscene cinematic ink yarn twine
        renpy dialoguetree storyline dialogueline voiceline dialogtext storytext dialogdata storydata dialogconfig
        storyconfig dialogvariable storyvariable dialogflag storyflag dialogcondition storycondition dialogtrigger
        storytrigger dialogsequence storysequence dialogbranch storybranch dialogchoice storychoice dialogending
        storyending dialogroute storyroute dialogflow storyflow dialogscript storyscript dialogmanager
        storymanager narrativedirector conversationmanager""".split())),
    ("任务标记", set("""quest quests mission missions task tasks objective objectives bounty contract questflag
        queststatus queststage queststep questcomplete missionstatus taskflag objectivecomplete questid missionid
        questlog questline mainquest sidequest dailyquest weeklyquest eventquest storyquest questprogress
        questrequirement missionobjective taskcomplete""".split())),
    ("经验", set("""exp experience xp exppoints expamount exptolevel nextlevel skillpoint skillpoints talentpoint
        talentpoints ap abilitypoint expgain expreward exppercent exprate expbonus expmultiplier expvalue
        currentexp totalexprequired""".split())),
    ("等级", set("""level lv lvl levels playerlevel characterlevel accountlevel rank tier grade prestige levelup
        maxlevel currentlevel levelrequirement levelprogress levelcap baselv targetlevel enemylevel bosslv
        itemlevel gearlevel weaponlevel skilllevel""".split())),
    ("货币", set("""gold coin coins money currency cash gem gems diamond diamonds jewel jewels credit credits
        token tokens wallet purse balance funds bucks dollars yuan rmb treasure riches wealth goldamount
        coincount currencyamount cashbalance gemcount diamondcount softcurrency hardcurrency premiumcurrency
        freecurrency paidcurrency""".split())),
    ("物品列表", set("""item items inventory bag backpack pouch slot slots equip equipment gear owned owneditems
        itemlist itemids inventorylist inventoryitems loot collectible consumable potion material materials
        resource resources craft recipe stash storage crate pack outfit costume skin cosmetic itemcount
        itemdata iteminfo itementry itemrecord itemslot equipmentlist gearlist ownedlist possessionlist
        carrieditems storeditems backpackitems""".split())),
]


def priority_concept(path: str, ftype: str = "") -> str | None:
    """返回重点关注概念中文名；非重点返回 None。"""
    leaf = _leaf(path)
    low = re.sub(r"^m_", "", leaf.lower()).lstrip("_")
    if _is_plumbing(low):
        return None
    words = _words(leaf)
    for concept, ws in PRIORITY_CONCEPTS:
        if any(_in(w, ws) for w in words):
            return concept
    return None