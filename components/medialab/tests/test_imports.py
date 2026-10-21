"""可选依赖（oss2 / websocket-server）缺失时公共入口仍可导入（P1 回归）。"""

import importlib
import importlib.abc
import sys
import unittest


class _BlockOptional(importlib.abc.MetaPathFinder):
    """拦截 oss2 / websocket_server 的导入，模拟未安装 extras 的环境。"""

    def __enter__(self):
        sys.meta_path.insert(0, self)
        return self

    def __exit__(self, *exc):
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        return False

    def find_spec(self, name, path=None, target=None):
        if name == "oss2" or name.startswith("oss2."):
            raise ImportError(f"blocked: {name}")
        if name == "websocket_server" or name.startswith("websocket_server."):
            raise ImportError(f"blocked: {name}")
        return None


class TestOptionalDepsLazy(unittest.TestCase):
    """每例都弹出已缓存的 medialab 子模块，在阻塞环境下重新导入；
    测试结束后恢复原模块对象，避免污染同进程其他测试（它们持有旧模块引用）。"""

    def setUp(self):
        self._saved = {m: sys.modules[m] for m in list(sys.modules)
                       if m == "medialab" or m.startswith("medialab.")}
        for m in self._saved:
            sys.modules.pop(m, None)

    def tearDown(self):
        for m in list(sys.modules):
            if m == "medialab" or m.startswith("medialab."):
                sys.modules.pop(m, None)
        sys.modules.update(self._saved)

    def test_import_medialab_without_extras(self):
        with _BlockOptional():
            self.assertIsNotNone(importlib.import_module("medialab"))

    def test_oss_raises_actionable_error_without_extras(self):
        with _BlockOptional():
            from medialab.oss import OssClient, OssConfig

            with self.assertRaises(RuntimeError) as ctx:
                OssClient(OssConfig(access_key_id="a", access_key_secret="s",
                                    endpoint="e", bucket="b", prefix="p/"))
            self.assertIn("medialab[cloud]", str(ctx.exception))

    def test_bridge_start_raises_actionable_error_without_extras(self):
        with _BlockOptional():
            from medialab.bridge import SessionBridgeServer

            s = SessionBridgeServer()
            with self.assertRaises(RuntimeError) as ctx:
                s.start()
            self.assertIn("medialab[bridge]", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
