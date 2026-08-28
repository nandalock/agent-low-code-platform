from typing import Callable


class Middleware:
    """Agent handler 中间件基类。洋葱模型：process 内部调用 next_handler 包裹下游。"""

    async def process(self, state: dict, node_id: str, next_handler: Callable) -> dict:
        raise NotImplementedError
