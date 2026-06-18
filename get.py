import json
import os
from time import time
from re import sub, compile, findall
from typing import List, Union, Literal, Optional
from datetime import datetime, timezone, timedelta

import asyncio
from httpx import AsyncClient

# 替换为实际的 BBS_URL
BBS_URL = "https://bbs-api.mihoyo.com"
CODES_FILE = "codes.json"
UPDATE_TIME_FILE = "update_time.txt"


class GenshinDataSource:
    TZ = timezone(timedelta(hours=8))

    def __init__(self):
        self.url = {
            'act_id_1': f'{BBS_URL}/painter/api/user_instant/list?offset=0&size=20&uid=75276539',
            'act_id_2': f'{BBS_URL}/painter/api/user_instant/list?offset=0&size=20&uid=75276550',
            'index': 'https://api-takumi.mihoyo.com/event/miyolive/index',
            'code': 'https://api-takumi-static.mihoyo.com/event/miyolive/refreshCode',
        }

    async def get_data(
        self,
        type: Literal['index', 'code', 'act_id_1', 'act_id_2'],
        data: dict = {},
    ) -> dict:
        async with AsyncClient() as client:
            try:
                if type == 'index':
                    res = await client.get(
                        self.url[type], headers={'x-rpc-act_id': data.get('actId', '')}
                    )
                elif type == 'code':
                    res = await client.get(
                        self.url[type],
                        params={
                            'version': data.get('version', ''),
                            'time': f'{int(time())}',
                        },
                        headers={'x-rpc-act_id': data.get('actId', '')},
                    )
                else:
                    res = await client.get(self.url[type])
                return res.json()
            except Exception as e:
                return {'error': f'[{e.__class__.__name__}] {type} 接口请求错误'}

    async def get_act_id(self, id: Literal['1', '2']) -> str:
        ret = await self.get_data('act_id_' + str(id))
        if ret.get('error') or ret.get('retcode') != 0:
            return ''
        act_id = ''
        keywords = ['前瞻特别节目']
        for p in ret['data']['list']:
            post = p.get('post', {}).get('post', {})
            if not post:
                continue
            if not all(word in post['subject'] for word in keywords):
                continue
            shit = json.loads(post['structured_content'])
            for segment in shit:
                link = segment.get('attributes', {}).get('link', '')
                if (
                    '观看' in segment.get('insert', '')
                    or '米游社直播间' in segment.get('insert', '')
                ) and link:
                    matched = findall(r'act_id=(.*?)\&', link)
                    if matched:
                        act_id = matched[0]
            if act_id:
                break
        return act_id

    async def get_live_data(self, act_id: str) -> dict:
        ret = await self.get_data('index', {'actId': act_id})
        if ret.get('error') or ret.get('retcode') != 0:
            return {'error': ret.get('error') or '前瞻直播数据异常'}
        live_raw = ret['data']['live']
        live_temp = json.loads(ret['data']['template'])
        live_data = {
            'code_ver': live_raw['code_ver'],
            'title': live_raw['title'].replace('特别直播', ''),
            'header': live_temp['kvDesktop'],
            'room': live_temp['liveConfig'][0]['desktop'],
        }
        # 统一格式到分钟，与 codes.json 现有格式一致
        start_dt = datetime.strptime(live_raw['start'], '%Y-%m-%d %H:%M:%S')
        live_data['start'] = start_dt.strftime('%Y-%m-%d %H:%M')
        return live_data

    async def get_code(self, version: str, act_id: str) -> Union[dict, List[dict]]:
        ret = await self.get_data('code', {'version': version, 'actId': act_id})
        if ret.get('error') or ret.get('retcode') != 0:
            return {'error': ret.get('error') or '兑换码数据异常'}
        code_data = []
        remove_tag = compile('<.*?>')
        for code_info in ret['data']['code_list']:
            item = {
                'items': sub(remove_tag, '', code_info['title']),
                'code': code_info['code'],
            }
            # 尝试提取过期时间（API 可能返回 expire_time / expire / end_time）
            expire = (
                code_info.get('expire_time')
                or code_info.get('expire')
                or code_info.get('end_time')
                or ''
            )
            if expire:
                item['expire'] = expire
            code_data.append(item)
        return code_data

    async def get_codes_with_live(self) -> Union[dict, List[dict]]:
        """获取完整的前瞻直播 & 兑换码数据（含 live_data）"""
        act_id = await self.get_act_id("1")
        if not act_id:
            act_id = await self.get_act_id("2")
            if not act_id:
                return {'error': '暂无前瞻直播资讯！'}
        live_data = await self.get_live_data(act_id)
        if live_data.get('error'):
            return {'error': live_data['error']}
        code_data = await self.get_code(live_data['code_ver'], act_id)
        if isinstance(code_data, dict) and code_data.get('error'):
            return {'error': code_data['error']}
        return {'live': live_data, 'codes': code_data}


# ---------- codes.json 操作 ----------

def load_codes() -> list:
    """加载已有 codes.json"""
    if os.path.exists(CODES_FILE):
        with open(CODES_FILE, 'r', encoding='utf-8') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_codes(data: list):
    """写入 codes.json"""
    with open(CODES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


def update_time_file():
    """更新 update_time.txt 为当前日期"""
    now = datetime.now(GenshinDataSource.TZ)
    with open(UPDATE_TIME_FILE, 'w', encoding='utf-8') as f:
        f.write(now.strftime('%Y%m%d') + '\n')


def clean_expired(entries: list) -> list:
    """按 valid 字段清理已过期的兑换码"""
    now = datetime.now(GenshinDataSource.TZ)
    kept = []
    removed = []
    for entry in entries:
        valid_str = entry.get('valid', '')
        if not valid_str:
            # 没有 valid 字段的不清理，保留
            kept.append(entry)
            continue
        try:
            valid_date = datetime.strptime(valid_str, '%Y-%m-%d').replace(
                tzinfo=GenshinDataSource.TZ
            )
            if now > valid_date:
                removed.append(entry)
            else:
                kept.append(entry)
        except ValueError:
            kept.append(entry)
    if removed:
        print(f'🧹 清理 {len(removed)} 条过期兑换码:')
        for r in removed:
            print(f'   - {r.get("title", "?")} (valid: {r.get("valid", "?")})')
    return kept


def get_existing_code_strings(entries: list) -> set:
    """收集所有已存在的兑换码字符串"""
    existing = set()
    for entry in entries:
        for c in entry.get('codes', []):
            existing.add(c)
    return existing


def is_new_code(existing_codes: set, code_str: str) -> bool:
    """判断兑换码是否为新码"""
    return code_str not in existing_codes


def build_entry(live_data: dict, new_codes: list) -> dict:
    """根据 API 数据构建一条 codes.json 条目"""
    code_strings = [c['code'] for c in new_codes if c.get('code')]
    items_list = [c['items'] for c in new_codes if c.get('items')]
    content = ' + '.join(items_list) if items_list else ''

    entry = {
        'title': live_data['title'],
        'content': content,
        'time': live_data.get('start', datetime.now(GenshinDataSource.TZ).strftime('%Y-%m-%d %H:%M')),
        'codes': code_strings,
    }

    # 前瞻直播一般周五开播，兑换码下周一 12:00 过期
    # 因为无法表达小时，写周二日期确保覆盖整个周一
    start_str = live_data.get('start', '')
    if start_str:
        try:
            start_date = datetime.strptime(start_str[:10], '%Y-%m-%d')
            valid_date = start_date + timedelta(days=4)  # 周五 → 周二
            entry['valid'] = valid_date.strftime('%Y-%m-%d')
        except ValueError:
            pass

    return entry


# ---------- 主程序 ----------

async def main():
    gds = GenshinDataSource()

    print('🔍 正在获取前瞻直播 & 兑换码...')
    result = await gds.get_codes_with_live()

    if isinstance(result, dict) and result.get('error'):
        print(f'❌ 获取失败: {result["error"]}')
        return

    live_data = result['live']
    new_codes = result['codes']

    print(f'📺 直播标题: {live_data["title"]}')
    print(f'🎁 API 返回 {len(new_codes)} 个兑换码:')
    for c in new_codes:
        print(f'   [{c.get("items", "?")}] {c.get("code", "?")}')

    # 构建新条目
    new_entry = build_entry(live_data, new_codes)
    if not new_entry['codes']:
        print('⚠️ 没有可用的兑换码，跳过写入')
        return

    # 加载现有数据
    existing = load_codes()
    print(f'\n📂 现有 codes.json 共 {len(existing)} 条记录')

    # 清理过期
    existing = clean_expired(existing)

    # 去重：检查新条目中的码是否已存在
    existing_code_strs = get_existing_code_strings(existing)
    dup_codes = [c for c in new_entry['codes'] if c in existing_code_strs]

    if dup_codes:
        print(f'⏭️ 以下兑换码已存在，跳过: {dup_codes}')
        # 如果全部重复，不写入
        if set(new_entry['codes']).issubset(existing_code_strs):
            print('✅ 所有兑换码均已存在，无需更新')
            return

    # 追加新条目
    existing.append(new_entry)
    save_codes(existing)
    update_time_file()
    print(f'✅ 已写入 codes.json（新增: {new_entry["codes"]}）')


if __name__ == "__main__":
    asyncio.run(main())
