# -*- coding: utf-8 -*-
import os
import requests
import base58
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse  # --- 引入 URL 解析库 ---

# --- 配置区 ---
DEFAULT_URLS_TO_FETCH = [
    "https://cdn04132025.gitlink.org.cn/api/v1/repos/leevi0321/null/raw/config.bin",
    "https://raw.githubusercontent.com/cmliu/cmliu/refs/heads/main/tvapi_config_json",  
    "https://raw.githubusercontent.com/666zmy/MoonTV/refs/heads/main/config.json", 
    "https://raw.githubusercontent.com/hafrey1/LunaTV-config/main/LunaTV-config.txt",
    "https://raw.githubusercontent.com/rapier15sapper/ew/refs/heads/main/test.json",
    "https://raw.githubusercontent.com/anaer/Meow/main/meow.json",  # TVBox 格式 (sites 字段), 约77个源
    "http://xhztv.top/4k.json",                                    # 小盒子4K (TVBox sites 格式)
    "http://xhztv.top/dc",                                         # 小盒子多仓 (urls 多仓格式, 18个子配置)
    "http://ztha.top/TVBox/GYCK.json",                             # 挺好分享多仓 (urls 多仓格式, 32个子配置)
    "http://xmbjm.fh4u.org/dc.txt"                                 # 拾光多仓 (urls 多仓格式)
]

# 多仓 (urls) 递归展开的最大深度, 防止多仓嵌套多仓导致无限递归
MAX_MULTI_STORE_DEPTH = 3

def load_source_urls():
    """
    读取上游源列表：优先使用环境变量 SOURCE_URLS（支持逗号或换行分隔），
    未设置时回退到默认列表，便于在不修改代码的情况下切换/增删上游。
    """
    env_urls = os.environ.get("SOURCE_URLS", "").strip()
    if env_urls:
        urls = [u.strip() for u in env_urls.replace(",", "\n").splitlines() if u.strip()]
        if urls:
            print(f"已从环境变量 SOURCE_URLS 加载 {len(urls)} 个上游源")
            return urls
    return DEFAULT_URLS_TO_FETCH

URLS_TO_FETCH = load_source_urls()

# --- 白名单配置 ---
ALLOWED_TOP_LEVEL_KEYS = {"cache_time", "api_site"}

OUTPUT_FILENAME = "config.json"
MAX_RETRIES = 2
RETRY_DELAY = 2
REQUEST_TIMEOUT = 8          # 单次请求超时(秒), 缩短以加快整体抓取
SUBFETCH_WORKERS = 8         # 多仓子配置并发抓取的线程数

def convert_sites_to_api_site(sites):
    """
    将 TVBox 格式的 sites 列表转换为本项目 api_site 字典格式。
    仅保留 api 字段为 http(s) 链接的站点（排除 JS/规则类源），
    例: {"key": "xxx", "name": "非凡", "api": "https://..."} -> {key: {"name":..., "api":...}}
    """
    converted = {}
    for index, site in enumerate(sites):
        if not isinstance(site, dict):
            continue
        api_link = site.get("api")
        if not isinstance(api_link, str) or not api_link.startswith("http"):
            # 跳过 JS 规则、jar 包等非采集接口源
            continue
        site_key = site.get("key") or site.get("id") or f"site_list_{index}"
        converted[site_key] = {
            "name": site.get("name", site_key),
            "api": api_link
        }
    return converted

def clean_content(raw_text):
    """
    清洗 TVBox 配置常见的头部垃圾：
    1. 去掉 UTF-8 BOM (\ufeff)
    2. 去掉以 // # * 开头的注释行（小盒子/老刘备等配置会在 JSON 前加说明注释）
    """
    text = raw_text.lstrip('\ufeff')
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("#") or stripped.startswith("*"):
            continue
        lines.append(line)
    return "\n".join(lines)

def parse_json_loose(text):
    """
    宽松 JSON 解析：
    1. 先尝试整体 json.loads
    2. 失败则用 raw_decode 提取第一个完整的 JSON 对象（忽略尾部杂散内容）
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = text.find("{")
        if start == -1:
            raise
        data, _ = decoder.raw_decode(text[start:])
        return data

def fetch_and_decode_url(url, depth=0):
    """
    从URL获取内容，智能判断是Base58还是明文JSON，然后解码/解析，并根据白名单进行过滤。
    depth: 当前递归深度, 用于多仓 (urls) 展开时限制嵌套层数
    """
    for attempt in range(MAX_RETRIES):
        try:
            print(f"正在尝试第 {attempt + 1}/{MAX_RETRIES} 次请求链接: {url}")
            response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
            response.raise_for_status()
            response.encoding = 'utf-8'
            raw_content = response.text.strip()

            if not raw_content:
                print(f"警告: 从 {url} 获取的内容为空。")
                return None

            # 先清洗 BOM/注释, 再尝试 Base58 (Base58 内容为单行编码, 清洗不影响)
            content = clean_content(raw_content)

            data = None
            try:
                print("...尝试将内容作为 Base58 解码...")
                decoded_bytes = base58.b58decode(content)
                decoded_string = decoded_bytes.decode('utf-8')
                data = json.loads(decoded_string)
                print("...成功将内容作为 Base58 解码。")
            except Exception:
                print("...Base58 解码失败，尝试直接作为明文 JSON 解析...")
                try:
                    data = parse_json_loose(content)
                    print("...成功将内容作为明文 JSON 解析。")
                except (json.JSONDecodeError, ValueError) as json_e:
                    print(f"错误: 内容既不是有效的Base58，也不是有效的JSON。错误信息: {json_e}")
                    return None

            print(f"成功解析链接内容: {url}")

            if isinstance(data, list):
                print("...检测到内容为列表(Array)格式，正在自动转换为字典格式...")
                converted_sites = {}
                for index, item in enumerate(data):
                    if isinstance(item, dict):
                        api_link = item.get("baseUrl") or item.get("api") or item.get("url")

                        if api_link:
                            site_key = item.get("id") or item.get("key") or item.get("name") or f"site_list_{index}"

                            converted_sites[site_key] = {
                                "name": item.get("name", site_key),
                                "api": api_link
                                # 在这里移除了强制写入空 detail 的逻辑，统交由后面的 main 模块处理
                            }

                if converted_sites:
                    data = {
                        "api_site": converted_sites
                    }
                    print(f"...成功从列表中提取并转换了 {len(converted_sites)} 个有效源。")
                else:
                    print("警告: 列表中未找到任何包含 'api', 'url' 或 'baseUrl' 字段的有效源。")
                    return None

            if isinstance(data, dict):
                filtered_data = {key: data[key] for key in ALLOWED_TOP_LEVEL_KEYS if key in data}

                # --- 支持 TVBox 格式的 sites 字段 ---
                # 例: Meow 仓库的 meow.json, 顶层无 api_site, 而是 sites 列表
                sites_list = data.get("sites")
                if isinstance(sites_list, list) and sites_list:
                    converted_sites = convert_sites_to_api_site(sites_list)
                    if converted_sites:
                        existing = filtered_data.get("api_site", {})
                        if not isinstance(existing, dict):
                            existing = {}
                        merged = dict(existing)
                        merged.update(converted_sites)
                        filtered_data["api_site"] = merged
                        print(f"...成功从 TVBox sites 字段转换了 {len(converted_sites)} 个有效源。")

                # --- 支持多仓格式 (urls 字段) 的递归展开 ---
                # 例: 小盒子多仓 http://xhztv.top/dc -> {"urls": [{"name": "...", "url": "..."}, ...]}
                # 每个子配置本身又是一个 TVBox 配置, 递归抓取后合并到 api_site
                urls_list = data.get("urls")
                if isinstance(urls_list, list) and urls_list:
                    if depth >= MAX_MULTI_STORE_DEPTH:
                        print(f"...已达到多仓最大递归深度 ({MAX_MULTI_STORE_DEPTH})，跳过 {url} 的子配置展开。")
                    else:
                        print(f"...检测到多仓 (urls) 格式，共 {len(urls_list)} 个子配置，开始递归展开 (深度 {depth + 1})...")
                        sub_api_sites = {}
                        # 收集可抓取的子配置
                        sub_targets = []
                        for sub in urls_list:
                            if not isinstance(sub, dict):
                                continue
                            sub_url = sub.get("url")
                            if not isinstance(sub_url, str) or not sub_url.startswith("http"):
                                continue
                            sub_targets.append((sub.get("name", ""), sub_url))
                        # 并发抓取所有子配置, 大幅缩短耗时
                        with ThreadPoolExecutor(max_workers=SUBFETCH_WORKERS) as executor:
                            future_map = {executor.submit(fetch_and_decode_url, sub_url, depth + 1): (sub_name, sub_url)
                                          for sub_name, sub_url in sub_targets}
                            for future in as_completed(future_map):
                                sub_name, sub_url = future_map[future]
                                try:
                                    sub_data = future.result()
                                except Exception as e:
                                    print(f"错误: 子配置 {sub_url} 抓取异常: {e}")
                                    continue
                                if not sub_data or not isinstance(sub_data.get("api_site"), dict):
                                    print(f"跳过无效子配置: {sub_name or sub_url}")
                                    continue
                                for sub_key, sub_value in sub_data["api_site"].items():
                                    if not isinstance(sub_value, dict):
                                        continue
                                    # 以子配置名作为前缀, 避免不同子配置间的键冲突
                                    prefixed_key = f"{sub_name}_{sub_key}" if sub_name else sub_key
                                    sub_value = dict(sub_value)
                                    if sub_name:
                                        sub_value["name"] = f"{sub_name}·{sub_value.get('name', sub_key)}"
                                    sub_api_sites[prefixed_key] = sub_value
                        if sub_api_sites:
                            existing = filtered_data.get("api_site", {})
                            if not isinstance(existing, dict):
                                existing = {}
                            merged = dict(existing)
                            merged.update(sub_api_sites)
                            filtered_data["api_site"] = merged
                            print(f"...多仓展开完成，共从 {len(sub_targets)} 个子配置中合并 {len(sub_api_sites)} 个有效源。")

                if not filtered_data:
                    print("警告: 解析后的内容中未找到白名单指定的键 (cache_time/api_site) 或可转换的 sites/urls 字段。")
                    return None
                print(f"内容已按白名单过滤，保留键: {list(filtered_data.keys())}")
                return filtered_data
            else:
                print("警告: 解析后的内容不是一个可按键过滤的字典。")
                return None

        except requests.exceptions.RequestException as req_e:
            print(f"错误：请求链接失败: {req_e}")
        except Exception as e:
            print(f"错误: 处理来自 {url} 的内容时发生未知错误: {e}")

        if attempt < MAX_RETRIES - 1:
            time.sleep(RETRY_DELAY)

    print(f"错误: 在 {MAX_RETRIES} 次尝试后，仍然无法处理链接: {url}")
    return None

def main():
    """
    主执行函数
    """
    print("--- 开始更新配置文件 ---")
    # 并发抓取所有顶层上游源
    clean_data_buffer = []
    with ThreadPoolExecutor(max_workers=len(URLS_TO_FETCH)) as executor:
        future_map = {executor.submit(fetch_and_decode_url, url): url for url in URLS_TO_FETCH}
        for future in as_completed(future_map):
            url = future_map[future]
            try:
                content = future.result()
            except Exception as e:
                print(f"错误: 抓取 {url} 异常: {e}")
                continue
            if content:
                clean_data_buffer.append(content)
    if not clean_data_buffer:
        print("错误: 所有链接内容均为空或无法按规则过滤，无法生成配置文件。")
        return
    print(f"\n过滤完成，共获得 {len(clean_data_buffer)} 组有效内容。准备提取 detail 字段并合并...")
    
    merged_api_sites = {}
    for item in clean_data_buffer:
        if "api_site" in item and isinstance(item.get("api_site"), dict):
            for key, value in item["api_site"].items():
                
                # ==========================================
                # --- 核心改动：全局统一自动提取 detail 字段 ---
                # ==========================================
                if isinstance(value, dict) and "api" in value:
                    api_url = value["api"]
                    try:
                        # 尝试解析 URL (例如把 http://abc.com/api.php 变成 http://abc.com)
                        parsed_uri = urlparse(api_url)
                        if parsed_uri.scheme and parsed_uri.netloc:
                            base_url = f"{parsed_uri.scheme}://{parsed_uri.netloc}"
                            value["detail"] = base_url
                        else:
                            # 如果提取不出合法域名，给个空字符串兜底
                            value.setdefault("detail", "")
                    except Exception:
                        value.setdefault("detail", "")
                else:
                    # 对于根本没有 api 字段的异常字典，也给个空值保持格式统一
                    if isinstance(value, dict):
                        value.setdefault("detail", "")
                # ==========================================

                new_key = key
                counter = 2
                while new_key in merged_api_sites:
                    new_key = f"{key}_{counter}"
                    counter += 1
                if new_key != key:
                    print(f"发现重复键 '{key}'，已重命名为 '{new_key}'")
                merged_api_sites[new_key] = value

    first_valid_cache_time = next((item.get("cache_time") for item in clean_data_buffer if "cache_time" in item), 7200)

    # ==========================================
    # --- 双格式输出: api_site (appleCMS) + sites (TVBox) ---
    # TVBox 系软件 (影视仓/OK影视/TVBoxOSC/FongMi) 解析的是顶层 sites 数组:
    #   [{"key": "...", "name": "...", "api": "..."}]
    # appleCMS 系软件解析的是 api_site 字典。两者同时输出以兼容全部播放器。
    # ==========================================
    tvbox_sites = []
    for key, value in merged_api_sites.items():
        if not isinstance(value, dict):
            continue
        site_entry = {
            "key": key,
            "name": value.get("name", key),
            "api": value.get("api", ""),
            # 影视仓/TVBoxOSC 硬性必填: type 缺失会抛异常导致整个配置解析失败
            # 0=xml 1=json 3=jar 4=remote; appleCMS 采集接口为 json
            "type": 1,
            # 显式声明搜索/筛选能力, 提高各播放器兼容性
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1
        }
        tvbox_sites.append(site_entry)

    final_config = {
        "cache_time": first_valid_cache_time,
        "api_site": merged_api_sites,
        "sites": tvbox_sites
    }
    print(f"已生成双格式配置: api_site {len(merged_api_sites)} 个源, sites {len(tvbox_sites)} 个源")
    try:
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(final_config, f, indent=4, ensure_ascii=False)
        print(f"成功！所有内容已通过重命名方式完整写入文件: {OUTPUT_FILENAME}")
    except IOError as e:
        print(f"错误: 写入文件 {OUTPUT_FILENAME} 失败: {e}")
    print("--- 更新任务结束 ---")

if __name__ == "__main__":
    main()
