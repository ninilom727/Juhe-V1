import json
import os

# --- 配置区 ---
INPUT_CONFIG_FILE = 'config.json'
OUTPUT_FILE = 'config.json'  # 过滤后的正常源覆盖原文件

# 定义用于识别成人内容的关键词列表 (不区分大小写)
ADULT_KEYWORDS = [
    # --- 原有基础词汇 (已清理重复项) ---
    'AV', '麻豆', '91', '杏吧', '森林', '淫水', '玉兔', '番号',
    '精品', '美少女', '老色逼', '色南国', '辣椒', '香奶儿', '鲨鱼',
    '黄色', '成人', '色情', '情色', '小猫咪', '快播', '细胞',
    'JKUN', 'souav', '小鸡', '155资源', 'AIvin', '优优', '奶香', 
    '幸资源', '桃花', '滴滴', '丝袜', '乐播', '奶子', '色猫', '最色', 
    '百万', '奥斯卡', '大地', '豆豆', '黑料', '香蕉',

    # --- 新增：常见厂牌/平台/暗号 ---
    '蜜桃', '糖心', '海角', '含羞草', '草榴', '1024', '微密', 
    '玩偶', '茄子', '香草', '秋霞', '探花', '红杏', '天仙', '吃瓜',
    
    # --- 新增：行业分类术语 ---
    '伦理', '福利', '午夜', '18禁', '十八禁', '限制级', '三级', 
    '无码', '有码', '步兵', '骑兵', '里番', '肉番',
    
    # --- 新增：常见诱导性词汇 ---
    '偷拍', '自拍', '女优', '巨乳', '人妻', '萝莉', '大尺度', '白嫖',
    
    # --- 新增：特殊符号 (很多源会在名字前面加 Emoji) ---
    '🔞'
]

def filter_adult_sources():
    """
    读取配置文件，根据关键词识别并删除成人API源，仅保留正常源写回配置文件。
    """
    print("--- 步骤 3: 开始过滤成人视频源 ---")
    
    # 检查输入文件是否存在
    if not os.path.exists(INPUT_CONFIG_FILE):
        print(f"错误: 输入文件 '{INPUT_CONFIG_FILE}' 未找到。请确保前序步骤已成功生成该文件。")
        return

    # 读取原始配置文件
    try:
        with open(INPUT_CONFIG_FILE, 'r', encoding='utf-8') as f:
            original_config = json.load(f)
    except json.JSONDecodeError:
        print(f"错误: 无法解析 '{INPUT_CONFIG_FILE}'。文件可能已损坏或格式不正确。")
        return
        
    # 创建新的配置模板，继承原始文件的元数据（如 cache_time）
    normal_config = original_config.copy()
    
    # 初始化空的 api_site 字典
    normal_sources = {}
    removed_adult_sources = {}
    
    all_sources = original_config.get('api_site', {})
    
    print(f"开始从 {len(all_sources)} 个源中进行过滤...")

    # 遍历所有源进行过滤
    for key, details in all_sources.items():
        is_adult = False
        source_name = details.get('name', '').lower() # 获取源名称并转为小写

        # 检查名称是否包含任何成人关键词
        for keyword in ADULT_KEYWORDS:
            if keyword.lower() in source_name:
                is_adult = True
                break # 找到一个关键词就足够了，跳出内层循环
        
        if is_adult:
            removed_adult_sources[key] = details
        else:
            normal_sources[key] = details

    # 将过滤后的源放回配置模板
    normal_config['api_site'] = normal_sources

    # 同步重建 TVBox 兼容的 sites 数组 (影视仓/OK影视/TVBoxOSC/FongMi 解析顶层 sites)
    # 注意: 影视仓/TVBoxOSC 硬性要求每个 site 含 type 字段, 缺失会抛异常导致解析失败
    # 0=xml 1=json 3=jar 4=remote; appleCMS 采集接口为 json
    normal_config['sites'] = [
        {
            "key": key,
            "name": details.get("name", key),
            "api": details.get("api", ""),
            "type": 1,
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1
        }
        for key, details in normal_sources.items()
        if isinstance(details, dict)
    ]

    # 打印被删除的成人源列表（便于在 CI 日志中查看）
    if removed_adult_sources:
        print(f"🗑️ 已识别并删除 {len(removed_adult_sources)} 个成人源:")
        for key, details in removed_adult_sources.items():
            print(f"  - {key}: {details.get('name', '?')}")

    # 写入过滤后的配置文件
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(normal_config, f, indent=4, ensure_ascii=False)
        print(f"处理完成: {len(normal_sources)} 个正常源已写入 '{OUTPUT_FILE}'"
              + (f"，{len(removed_adult_sources)} 个成人源已删除" if removed_adult_sources else ""))
    except IOError as e:
        print(f"错误: 写入 '{OUTPUT_FILE}' 失败: {e}")

if __name__ == "__main__":
    filter_adult_sources()
