"""在容器内测试 token 获取（完全复刻 fetch_chats.py）"""
import asyncio
import hashlib
import json
import random
import string
import time

import httpx

TOKEN_API = "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/"
APP_KEY = "34839810"
IM_APP_KEY = "444e9908a51d1cb236a27862abc769c9"

COOKIE = """_samesite_flag_=true; cookie2=14054001760dd2c7ae58876211758c97; t=b35762ae0b8a046cf1b6a954d8dc2462; _tb_token_=e6dfb37ee5041; tracknick=tb90554653; havana_lgc2_77=eyJoaWQiOjIyMDEwMTQ3NDU1NDAsInNnIjoiMDdiMzE0MGQyZmMwNzEyODZjNzhlMTkwYWM5YjliMDUiLCJzaXRlIjo3NywidG9rZW4iOiIxdjRIdmZGTzQ2QnJYTldtSEYwRVlpQSJ9; _hvn_lgc_=77; havana_lgc_exp=1784215350878; xlly_s=1; sgcookie=E100iPoCBey9JLp35iD9n9NxtlL0MQEwJ9c4rDOGvXXUeB3GwyECERp3kUmrTHWLt0uioaUOlXIt5tEtEcqknyExJ5xJX6x%2FlcfQ5iA5I0ixPmA%3D; csg=815bff53; unb=2201014745540; sdkSilent=1782052033928; mtop_partitioned_detect=1; _m_h5_tk=88529de8ce38aca255374843cf22a82a_1782036916257; _m_h5_tk_enc=92ad3f4b1073373ae59461fd43a3d750; tfstk=g3IKZRm3X5V3dDZ_MD4GrypKOx2gSPXEK6WjqQAnP1COhtlHPMxhw4ChU_Y7THjR6HSG-0A3T3B5i3F0ioqcL982VSV062wYL397qQaMRCgT9xN0ioqgRAt89SXoAjpJfLR6dLiIARK6nKtWdD1W1F9XnXtWV_w9fKv-Pb9Wdhg6UCOWV315CRpk1ptWV_6_BLlxq8AszQiR7StreVmB_0i5XpL_S9OsDpj9pFOfdMnSVYJpJI6B6W4egeL1UUIrU0LfdZ5yh6GQwQSOCM_fG50HOZpRnaBQActhY9_XPiNrPsKdwEsBWYwRK3QeXdsbEqRGXBf5vFwmDUx1mEtCSPPHrHpANM5Ke01fItje7gFx1Q7H3h9R4-gJwUsz3ijYf2hDML0QBR3rze9ZGaZMsBJc3gp9iJUoz48gldd0BR3rze9wBI2GD4uySr1."""

def h5tk(cs):
    for p in cs.split("; "):
        if p.startswith("_m_h5_tk="):
            return p.split("=",1)[1].split("_")[0]
    return ""

def sign(ts, t, d):
    return hashlib.md5(f"{t}&{ts}&{APP_KEY}&{d}".encode()).hexdigest()

def mid():
    return f"cli-{int(time.time()*1000)}-{random.randbytes(4).hex()}"

async def main():
    cs = COOKIE
    ck = {}
    for p in cs.split("; "):
        if "=" in p: k, v = p.split("=", 1); ck[k] = v
    uid = ck.get("unb", "")
    did = f"{uid}_{''.join(random.choices(string.hexdigits.lower(), k=32))}"
    print(f"UID: {uid}")
    print(f"DID: {did}")

    print("[1] Token...")
    ts = str(int(time.time()*1000))
    dv = json.dumps({"appKey": IM_APP_KEY, "deviceId": did}, separators=(",",":"))
    sg = sign(ts, h5tk(cs), dv)
    hd = {"accept":"application/json","content-type":"application/x-www-form-urlencoded",
          "cookie":cs,"referer":"https://www.goofish.com/","origin":"https://www.goofish.com",
          "user-agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"}
    print(f"  sign={sg[:20]}...")
    print(f"  t={ts}")
    print(f"  token_part={h5tk(cs)}")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as s:
        r = await s.post(TOKEN_API, params={
            "jsv":"2.7.2","appKey":APP_KEY,"t":ts,"sign":sg,"v":"1.0",
            "type":"originaljson","accountSite":"xianyu","dataType":"json",
            "timeout":"20000",
            "api":"mtop.taobao.idlemessage.pc.login.token",
            "sessionOption":"AutoLoginOnly",
            "spm_cnt":"a21ybx.im.0.0",
            "spm_pre":"a21ybx.home.sidebar.1.4c053da6vYwnmf",
            "log_id":"4c053da6vYwnmf",
        }, data={"data":dv}, headers=hd)
        result = r.json()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "accessToken" in result.get("data", {}):
        print("OK! accessToken:", result["data"]["accessToken"][:50] + "...")
    else:
        print("FAIL:", result.get("ret"))

asyncio.run(main())
