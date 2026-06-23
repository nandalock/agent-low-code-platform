"""测试脚本：监听闲鱼消息并打印

用法：
    python test_xianyu.py
"""
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

from backend.xianyu.live import XianyuLive

COOKIE = "_samesite_flag_=true; cookie2=14054001760dd2c7ae58876211758c97; t=b35762ae0b8a046cf1b6a954d8dc2462; _tb_token_=e6dfb37ee5041; tracknick=tb90554653; havana_lgc2_77=eyJoaWQiOjIyMDEwMTQ3NDU1NDAsInNnIjoiMDdiMzE0MGQyZmMwNzEyODZjNzhlMTkwYWM5YjliMDUiLCJzaXRlIjo3NywidG9rZW4iOiIxdjRIdmZGTzQ2QnJYTldtSEYwRVlpQSJ9; _hvn_lgc_=77; havana_lgc_exp=1784215350878; mtop_partitioned_detect=1; _m_h5_tk=fd3529b2cfffb3986ff5bdfd27f7a625_1781972832330; _m_h5_tk_enc=d051c15b588774ad959862e8486448f7; xlly_s=1; sgcookie=E100iPoCBey9JLp35iD9n9NxtlL0MQEwJ9c4rDOGvXXUeB3GwyECERp3kUmrTHWLt0uioaUOlXIt5tEtEcqknyExJ5xJX6x%2FlcfQ5iA5I0ixPmA%3D; csg=815bff53; unb=2201014745540; sdkSilent=1782052033928; tfstk=gHusBpXwdOX649A9HORFNB0j2W4bhB8PlsNxZjQNMPUOHth8LAya7IYbHA2E7RlaW1mnUbQZ7h4VlP40kLJyzUPZsr4xG0JaWOjLZ5T4MGFYSyFYUbQyzUli61P9aXLr0MeSvSeYklEA9We36GCtHlBL9JV0MOFAWBGLKJBTHZEx9MFb6rexHrdI9JVbk5HYkBGLKShVeTPXgRG6i8gOiztNtfwCkZ3QOK2o1KQ4O2V_f8hTzNQ9s5Z_efeBQh2ufkh0cqfcluhtqxV-BOpbn2GKW0HJLGytVSGrcfLOJPui60ExyKjxFPMb2VECMIGalc4Q94dVqJuQQx3_vQ770y3z2Pnexdr457MxSA1fkYhEaVqnlL6LncPuJSgDwiwbDgkdz8trHi1QqZNQUBOCmibj5y8rNn3xclF31yOBOOaLXWVQUBOCmiqTtWSBOB6_J"


async def main():
    live = XianyuLive(COOKIE)
    await live.main()


if __name__ == "__main__":
    asyncio.run(main())
