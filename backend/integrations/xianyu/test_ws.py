"""在容器内测试 LWP 请求（完全复刻 fetch_chats.py 的 WS 逻辑）"""
import asyncio
import hashlib
import json
import os
import random
import ssl
import string
import time

import aiohttp

TOKEN_API = "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/"
APP_KEY = "34839810"
IM_APP_KEY = "444e9908a51d1cb236a27862abc769c9"
WS_URL = "wss://wss-goofish.dingtalk.com/"

COOKIE = """_samesite_flag_=true; cookie2=14054001760dd2c7ae58876211758c97; t=b35762ae0b8a046cf1b6a954d8dc2462; _tb_token_=e6dfb37ee5041; xlly_s=1; sdkSilent=1782052033928; mtop_partitioned_detect=1; _m_h5_tk=8b5d2f1213f9b3943597e5f1ee1845e1_1782042146781; _m_h5_tk_enc=5b9bb4681c7f4da9fd3c0466055a2dfa; sgcookie=E100%2F5h2gACk6ol9ftENWnt3l1k2Nj4OW4Ji78rOY59sPTvGnMrH1u7wBh4IvS%2BTg3XKzlZBIjv0MQEsMj%2B%2FdP0xYwgrbZsUeIYSfubPORuwckY%3D; tracknick=xy077311859140; csg=f21b541d; unb=2219617979751; tfstk=gX1ZwCbup5FNnzqlzaA4Uwwr63d9GIrSustXoZbD5hxi1ficLgjpft66fqWFmMp6jO_sW9I5rf1_BIG26Id0VuN7NF_9MIcGN_okXwbpoyTgV1q-5Id0VkMIidFyMGiiEWQc-y8XlcmcmFc38E8XifvmsvmHvEADidDgtHY2rFmGmI4F-HLDiFjDIy7HvEADmjCRXinWrd4_3WeRE5YeQ3bMYj7OTFvZBNxEijfFLd-oVHlmi68NkLJXTbyXqTK6FnS3OfRV-ESeFOrr_i7cytvFgc41qMXVmeBL0x-PnN6RnpzgswRwbg5pUSZ9_tjOgdBZkzQHsMBJ2d2Lve51NK-JLDqGRw-MUtS_vjx1UwjeF6iINQXRYsJkZg-mDevSnsBZmxJMJe-78y81QheKzjHbxxHvKLYeVPpmHxpMJe-78ykxHdhk83a9n"""

def h5tk(cs):
    for p in cs.split("; "):
        if p.startswith("_m_h5_tk="):
            return p.split("=",1)[1].split("_")[0]
    return ""

def sign(ts, t, d):
    return hashlib.md5(f"{t}&{ts}&{APP_KEY}&{d}".encode()).hexdigest()

def mid():
    return f"cli-{int(time.time()*1000)}-{os.urandom(4).hex()}"

async def main():
    cs = COOKIE
    ck = {}
    for p in cs.split("; "):
        if "=" in p: k, v = p.split("=", 1); ck[k] = v
    uid = ck.get("unb", "")
    did = f"{uid}_{''.join(random.choices(string.hexdigits.lower(), k=32))}"
    print(f"UID: {uid}")
    print(f"DID: {did}")

    # Token
    print("[1] Token...")
    ts = str(int(time.time()*1000))
    dv = json.dumps({"appKey": IM_APP_KEY, "deviceId": did}, separators=(",",":"))
    sg = sign(ts, h5tk(cs), dv)
    hd = {"accept":"application/json","content-type":"application/x-www-form-urlencoded",
          "cookie":cs,"referer":"https://www.goofish.com/","origin":"https://www.goofish.com",
          "user-agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"}
    async with aiohttp.ClientSession() as s:
        async with s.post(TOKEN_API, params={
            "jsv":"2.7.2","appKey":APP_KEY,"t":ts,"sign":sg,"v":"1.0",
            "type":"originaljson","accountSite":"xianyu","dataType":"json",
            "timeout":"20000",
            "api":"mtop.taobao.idlemessage.pc.login.token",
            "sessionOption":"AutoLoginOnly",
            "spm_cnt":"a21ybx.im.0.0",
            "spm_pre":"a21ybx.home.sidebar.1.4c053da6vYwnmf",
            "log_id":"4c053da6vYwnmf",
        }, data={"data":dv}, headers=hd, timeout=aiohttp.ClientTimeout(total=15)) as r:
            result = await r.json(content_type=None)
    if "accessToken" not in result.get("data", {}):
        print(f"  Token FAIL: {result.get('ret')}")
        return
    token = result["data"]["accessToken"]
    print(f"  Token OK: {token[:30]}...")

    # WS
    print("[2] WS...")
    wh = {"Cookie":cs,"Host":"wss-goofish.dingtalk.com","Connection":"Upgrade",
          "Pragma":"no-cache","Cache-Control":"no-cache","Origin":"https://www.goofish.com",
          "Accept-Encoding":"gzip, deflate, br, zstd","Accept-Language":"zh-CN,zh;q=0.9",
          "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36"}
    pending = {}
    ctx = ssl.create_default_context()

    async def rloop(ws):
        try:
            async for m in ws:
                if m.type != aiohttp.WSMsgType.TEXT: continue
                msg = json.loads(m.data)
                h = msg.get("headers",{})
                m_id = h.get("mid","")
                lwp = msg.get("lwp","?")
                b = msg.get("body",{})
                btype = "list" if isinstance(b, list) else "dict"
                bpreview = str(b)[:200]
                print(f"  [<-] lwp={lwp} mid={m_id[:30]} body={btype} {bpreview}")
                # ACK
                ack = {"code":200,"headers":{"mid":m_id,"sid":h.get("sid","")}}
                for k in ("app-key","ua","dt"):
                    if k in h: ack["headers"][k] = h[k]
                try: await ws.send_json(ack)
                except: pass
                if m_id and m_id in pending:
                    print(f"  [匹配] mid={m_id[:30]} RESOLVED!")
                    pending[m_id].set_result(msg)
        except asyncio.CancelledError: pass

    async def saw(ws, msg, to=15):
        m_id = msg["headers"]["mid"]
        f = asyncio.get_event_loop().create_future()
        pending[m_id] = f
        await ws.send_json(msg)
        try: return await asyncio.wait_for(f, timeout=to)
        except asyncio.TimeoutError:
            print(f"  [超时] {msg.get('lwp')} mid={m_id[:30]}")
            return None
        finally: pending.pop(m_id, None)

    async with aiohttp.ClientSession() as sess:
        async with sess.ws_connect(WS_URL, headers=wh, ssl=ctx, heartbeat=30) as ws:
            print("  WS 已连接")
            rt = asyncio.create_task(rloop(ws))

            # Reg
            print("[3] 注册...")
            await ws.send_json({
                "lwp":"/reg","headers":{
                    "cache-header":"app-key token ua wv",
                    "app-key":IM_APP_KEY,"token":token,
                    "ua":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "dt":"j","wv":"im:3,au:3,sy:6","sync":"0,0;0;0;",
                    "did":did,"mid":mid(),
                }})
            await asyncio.sleep(1)
            nw = int(time.time()*1000)
            await ws.send_json({
                "lwp":"/r/SyncStatus/ackDiff","headers":{"mid":mid()},
                "body":[{"pipeline":"sync","tooLong2Tag":"PNM,1","channel":"sync",
                         "topic":"sync","highPts":0,"pts":nw*1000,"seq":0,"timestamp":nw}],
            })
            print("  注册完成，等待推送...")
            await asyncio.sleep(5)

            # 拉会话
            print("[4] 拉会话...")
            resp = await saw(ws, {
                "lwp":"/r/Conversation/listNewestPagination",
                "headers":{"mid":mid()},
                "body":[9007199254740991, 5],
            }, to=15)
            if resp:
                convs = resp.get("body",{}).get("userConvs",[])
                print(f"  拿到 {len(convs)} 个会话:")
                for i,c in enumerate(convs,1):
                    u = c.get("user",{})
                    print(f"    {i}. [{u.get('nick','?')}] {c.get('conv',{}).get('latestMsg',{}).get('text','?')[:40]}")
            else:
                print("  拉会话超时或无响应")

            rt.cancel()
            try: await rt
            except asyncio.CancelledError: pass

asyncio.run(main())
