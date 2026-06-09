import json
from time import time
from re import sub, compile, findall
from typing import List, Union, Literal, Optional
from datetime import datetime, timezone, timedelta

import asyncio
from httpx import AsyncClient

# 替换为实际的 BBS_URL
BBS_URL = "https://bbs-api.mihoyo.com"

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
        now = datetime.fromtimestamp(time(), self.TZ)
        start = datetime.strptime(live_raw['start'], '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=self.TZ
        )
        if now < start:
            live_data['start'] = live_raw['start']
        return live_data

    async def get_code(self, version: str, act_id: str) -> Union[dict, List[dict]]:
        ret = await self.get_data('code', {'version': version, 'actId': act_id})
        if ret.get('error') or ret.get('retcode') != 0:
            return {'error': ret.get('error') or '兑换码数据异常'}
        code_data = []
        remove_tag = compile('<.*?>')
        for code_info in ret['data']['code_list']:
            code_data.append(
                {
                    'items': sub(remove_tag, '', code_info['title']),
                    'code': code_info['code'],
                }
            )
        return code_data

    async def get_code_msg(self) -> str:
        act_id = await self.get_act_id("1")
        if not act_id:
            act_id = await self.get_act_id("2")
            if not act_id:
                return '暂无前瞻直播资讯！'
        live_data = await self.get_live_data(act_id)
        if live_data.get('error'):
            return live_data['error']
        code_data = await self.get_code(live_data['code_ver'], act_id)
        # if isinstance(code_data, dict):
        #     return code_data['error']
        # code_msg = f'{live_data["title"]}\n'
        # for index, code in enumerate(code_data, 1):
        #     if code.get('code'):
        #         code_msg += f'{code["items"]}:\n{code["code"]}\n'
        #     else:
        #         code_msg += f'第 {index} 个兑换码暂未发放\n'
        # return code_msg.strip()
        return code_data

# 示例主程序
async def main():
    gds = GenshinDataSource()
    msg = await gds.get_code_msg()
    print(msg)

if __name__ == "__main__":
    asyncio.run(main())