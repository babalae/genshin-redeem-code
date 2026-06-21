# -*- coding: utf-8 -*-
"""
Hoyolab 兑换码爬虫
功能：无需登录，从 hoyolab 官方 API 获取指定游戏的兑换码信息
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests


# API 配置常量
API_URL = 'https://bbs-api-os.hoyolab.com/community/painter/wapi/circle/channel/guide/material'
DEFAULT_TIMEOUT = 15

# 基础请求头（模拟浏览器访问）
BASE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Origin': 'https://www.hoyolab.com',
    'Referer': 'https://www.hoyolab.com/',
    'Accept': 'application/json, text/plain, */*',
    'x-rpc-app_version': '1.5.0',
    'x-rpc-client_type': '4',
}

# 游戏 ID 映射表（常见游戏）
GAME_ID_MAP = {
    '2': '原神',
    '6': '崩坏3',
    '8': '崩坏：星穹铁道',
    '4': '未定事件簿',
}

# 奖励数量 → 物品名称映射
BONUS_NAME_MAP = {
    100: '原石',
    5: '大英雄的经验',
    10: '精锻用魔矿',
    50000: '摩拉',
}


def fetch_raw_data(game_id='2', timeout=DEFAULT_TIMEOUT):
    """
    从 hoyolab API 拉取原始数据

    Args:
        game_id: 游戏 ID，默认为 '2'（原神）
        timeout: 请求超时时间，单位秒

    Returns:
        dict: API 返回的原始 JSON 数据

    Raises:
        requests.exceptions.RequestException: 网络请求失败时抛出
        ValueError: API 返回非 0 retcode 时抛出
    """
    params = {'game_id': str(game_id)}
    headers = dict(BASE_HEADERS)

    response = requests.get(API_URL, headers=headers, params=params, timeout=timeout)
    response.raise_for_status()
    raw_data = response.json()

    retcode = raw_data.get('retcode', -1)
    message = raw_data.get('message', '')
    if retcode != 0:
        raise ValueError('API 返回错误：retcode=%s, message=%s' % (retcode, message))

    return raw_data


def extract_exchange_groups(raw_data):
    """
    从原始数据中提取兑换码分组（每组为一个 exchange_group）

    Args:
        raw_data: fetch_raw_data 返回的原始数据 dict

    Returns:
        list[dict]: 每个元素是一个 exchange_group，
            包含 bonuses(兑换码列表)、bonuses_summary(聚合奖励)、
            offline_at、title、game_id 等字段
    """
    groups = []
    modules = raw_data.get('data', {}).get('modules', [])

    for module in modules:
        exchange_group = module.get('exchange_group')
        if not exchange_group:
            continue

        groups.append({
            'game_id': exchange_group.get('game_id', ''),
            'title': exchange_group.get('title', ''),
            'offline_at': exchange_group.get('offline_at', 0),
            'bonuses': exchange_group.get('bonuses', []),
            'bonuses_summary': exchange_group.get('bonuses_summary', {}),
        })

    return groups


def format_bonus_name(bonus_num):
    """将数量映射为物品名称"""
    return BONUS_NAME_MAP.get(int(bonus_num), '%d件物品' % int(bonus_num))


def format_offline_date(offline_at_timestamp):
    """
    将 Unix 时间戳转为北京时间的日期字符串（用于 valid 字段）

    Args:
        offline_at_timestamp: 秒级 Unix 时间戳

    Returns:
        str: 例如 '2026-06-21'，失败返回空字符串
    """
    try:
        ts = int(offline_at_timestamp)
        if ts <= 0:
            return ''
        tz = timezone(timedelta(hours=8))
        dt = datetime.fromtimestamp(ts, tz=tz)
        return dt.strftime('%Y-%m-%d')
    except (ValueError, TypeError, OSError):
        return ''


def process_codes(groups, game_id):
    """
    处理兑换码分组，筛选有效码并构建为 get.py 相同格式的条目

    按 bonus_num 强制聚合（同种奖励的 per_code 数量一致），
    用 BONUS_NAME_MAP 映射名称。

    Args:
        groups: extract_exchange_groups 返回的分组列表
        game_id: 游戏 ID

    Returns:
        dict: 与 get.py 的 codes.json 条目格式一致，
              {'title', 'content', 'time', 'codes', 'valid'}，
              无有效兑换码时返回 None
    """
    all_code_strings = []
    # { per_code_bonus_num: total } — 按 bonus_num 聚合
    aggregated = {}
    earliest_offline = None

    for group in groups:
        bonuses = group.get('bonuses', [])
        offline_at = group.get('offline_at', 0)

        for b in bonuses:
            code_str = b.get('exchange_code', '').strip()
            status = b.get('code_status', '')
            if not code_str or status != 'ON':
                continue
            all_code_strings.append(code_str)

            for item in b.get('icon_bonuses', []):
                num = int(item.get('bonus_num', 0))
                aggregated[num] = aggregated.get(num, 0) + num

        # 取最早的过期时间
        try:
            ts = int(offline_at)
            if ts > 0 and (earliest_offline is None or ts < earliest_offline):
                earliest_offline = ts
        except (ValueError, TypeError):
            pass

    if not all_code_strings:
        return None

    # 按 bonus_num 映射名称，拼成 content
    items_parts = []
    for per_code, total in aggregated.items():
        name = format_bonus_name(per_code)
        items_parts.append('%s*%d' % (name, total))
    content = ' + '.join(items_parts)

    tz = timezone(timedelta(hours=8))

    entry = {
        'title': '[国际服]前瞻直播',
        'content': content,
        'time': datetime.now(tz).strftime('%Y-%m-%d %H:%M'),
        'codes': all_code_strings,
    }

    # valid: 基于最早的 offline_at
    valid_date = format_offline_date(earliest_offline) if earliest_offline else ''
    if valid_date:
        entry['valid'] = valid_date

    return entry


# ---------- codes.json 操作 ----------

CODES_FILE = 'codes.json'
UPDATE_TIME_FILE = 'update_time.txt'


def load_codes_json():
    """加载已有 codes.json"""
    if os.path.exists(CODES_FILE):
        with open(CODES_FILE, 'r', encoding='utf-8') as fp:
            try:
                return json.load(fp)
            except json.JSONDecodeError:
                return []
    return []


def save_codes_json(data):
    """写入 codes.json"""
    with open(CODES_FILE, 'w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write('\n')


def update_time_file():
    """更新 update_time.txt"""
    tz = timezone(timedelta(hours=8))
    with open(UPDATE_TIME_FILE, 'w', encoding='utf-8') as fp:
        fp.write(datetime.now(tz).strftime('%Y%m%d') + '\n')


def clean_expired(entries):
    """清理已过期的条目"""
    tz = timezone(timedelta(hours=8))
    today = datetime.now(tz).date()
    kept = []
    for entry in entries:
        valid_str = entry.get('valid', '')
        if not valid_str:
            kept.append(entry)
            continue
        try:
            valid_date = datetime.strptime(valid_str, '%Y-%m-%d').date()
            if today <= valid_date:
                kept.append(entry)
        except ValueError:
            kept.append(entry)
    return kept


def merge_to_codes_json(new_entry=None):
    """合并新条目到 codes.json，仅与同 title 条目去重，国际服/国服互不干扰"""
    existing = load_codes_json()
    cleaned = clean_expired(existing)
    has_expired = len(cleaned) != len(existing)
    existing = cleaned

    if new_entry is None:
        if has_expired:
            save_codes_json(existing)
            update_time_file()
            print('已将过期兑换码清理结果写入 codes.json')
        return

    new_title = new_entry.get('title', '')

    # 只收集同 title 条目下的已有兑换码（大小写不敏感）
    seen = set()
    for entry in existing:
        if entry.get('title', '') == new_title:
            for c in entry.get('codes', []):
                seen.add(c.lower())

    # 去重
    new_codes = new_entry.get('codes', [])
    fresh = [c for c in new_codes if c.lower() not in seen]
    if not fresh:
        if has_expired:
            save_codes_json(existing)
            update_time_file()
            print('已将过期兑换码清理结果写入 codes.json')
        print('[%s] 所有兑换码均已存在，跳过' % new_title)
        return

    new_entry['codes'] = fresh
    existing.append(new_entry)
    save_codes_json(existing)
    update_time_file()
    print('[%s] 已写入 codes.json: %s' % (new_title, fresh))


def main():
    """主入口：拉取 hoyolab 兑换码，处理为 get.py 相同格式并写入 codes.json"""
    game_id = '2'
    if len(sys.argv) >= 2:
        game_id = sys.argv[1]

    # 1) 拉取原始数据
    try:
        raw_data = fetch_raw_data(game_id=game_id)
    except requests.exceptions.RequestException as exc:
        print('网络请求失败: %s' % exc)
        sys.exit(1)
    except ValueError as exc:
        print('API 返回异常: %s' % exc)
        sys.exit(1)

    # 2) 提取兑换码分组
    groups = extract_exchange_groups(raw_data)

    # 3) 筛选 + 映射 + 构建 get.py 格式
    result = process_codes(groups, game_id)

    if result is None:
        print('当前没有可用的兑换码。')
        merge_to_codes_json()
        return

    # 4) 输出并写入 codes.json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    merge_to_codes_json(result)


if __name__ == '__main__':
    main()
