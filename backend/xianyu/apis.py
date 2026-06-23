import asyncio
import json
import logging
import random
import time

import aiohttp

from backend.xianyu.utils import generate_sign

logger = logging.getLogger(__name__)

TOKEN_API = "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.login.token/1.0/"
REFRESH_TOKEN_API = "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.loginuser.get/1.0/"


class XianyuApis:
    _last_fail_time: float = 0  # 类级别，所有实例共享

    def __init__(self, cookies_str: str, device_id: str):
        self.cookies_str = cookies_str
        self.device_id = device_id
        self._cached_token: str = ""
        self._cached_device_id: str = ""
        self._last_token_time: float = 0

    def _h5tk(self):
        for p in self.cookies_str.split("; "):
            if p.startswith("_m_h5_tk="):
                return p.split("=", 1)[1].split("_")[0]
        return ""

    async def get_token_async(self) -> str:
        """异步获取 accessToken（与参考项目一致的请求参数）"""
        if self._cached_token and (time.time() - self._last_token_time) < 7200:
            logger.info("get_token_async: 使用缓存 token")
            return self._cached_token

        def _do_request():
            import requests as req
            ts = str(int(time.time() * 1000))
            data_val = json.dumps(
                {"appKey": "444e9908a51d1cb236a27862abc769c9", "deviceId": self.device_id},
                separators=(",", ":"),
            )
            token_part = self._h5tk()
            sg = generate_sign(ts, token_part, data_val)
            params = {
                "jsv": "2.7.2",
                "appKey": "34839810",
                "t": ts,
                "sign": sg,
                "v": "1.0",
                "type": "originaljson",
                "accountSite": "xianyu",
                "dataType": "json",
                "timeout": "20000",
                "api": "mtop.taobao.idlemessage.pc.login.token",
                "sessionOption": "AutoLoginOnly",
                "spm_cnt": "a21ybx.im.0.0",
                "spm_pre": "a21ybx.home.sidebar.1.4c053da6vYwnmf",
                "log_id": "4c053da6vYwnmf",
            }
            headers = {
                "accept": "application/json",
                "content-type": "application/x-www-form-urlencoded",
                "cookie": self.cookies_str,
                "referer": "https://www.goofish.com/",
                "origin": "https://www.goofish.com",
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/146.0.0.0 Safari/537.36"
                ),
            }
            r = req.post(TOKEN_API, params=params, data={"data": data_val}, headers=headers, timeout=30)
            return r.json()

        await asyncio.sleep(random.uniform(1, 3))

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, _do_request)
        except Exception as e:
            logger.warning(f"get_token 网络异常: {e}")
            return self._cached_token

        ret = result.get("ret", [])
        ret_str = str(ret)

        if "FAIL_SYS_SESSION_EXPIRED" in ret_str:
            logger.warning("Cookie session 已过期")
            return self._cached_token

        token = result.get("data", {}).get("accessToken", "")

        if token:
            self._cached_token = token
            self._last_token_time = time.time()
            logger.info(f"get_token_async 成功: {token[:30]}...")
            return token

        if any(k in ret_str for k in ("RGV587", "FAIL_SYS_USER_VALIDATE")):
            logger.warning(f"get_token 失败: {ret_str[:120]}")
        else:
            logger.warning(f"get_token 失败: ret={ret}")

        return self._cached_token

    def refresh_token(self) -> dict:
        """同步刷新 token（仅供 user_alive 线程调用）"""
        # user_alive 是同步线程，用简单的 requests 调用
        import urllib.request, urllib.parse
        ts = str(int(time.time() * 1000))
        dv = "{}"
        token_part = self._h5tk()
        sg = generate_sign(ts, token_part, dv)
        params = {
            "jsv": "2.7.2", "appKey": "34839810", "t": ts, "sign": sg, "v": "1.0",
            "type": "originaljson", "accountSite": "xianyu", "dataType": "json",
            "timeout": "20000", "api": "mtop.taobao.idlemessage.pc.loginuser.get",
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": "a21ybx.im.0.0",
            "spm_pre": "a21ybx.home.sidebar.1.4c053da6vYwnmf",
            "log_id": "4c053da6vYwnmf",
        }
        url = REFRESH_TOKEN_API + "?" + "&".join(f"{k}={v}" for k, v in params.items())
        post_data = urllib.parse.urlencode({"data": dv}).encode("utf-8")
        req = urllib.request.Request(url, data=post_data, headers={
            "accept": "application/json",
            "content-type": "application/x-www-form-urlencoded",
            "cookie": self.cookies_str,
            "referer": "https://www.goofish.com/",
            "origin": "https://www.goofish.com",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"refresh_token 异常: {e}")
            return {}

    async def close(self):
        pass
