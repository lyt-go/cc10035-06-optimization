"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import cross_module, dispatch, resource

# dispatch 的固定路径必须先注册，避免被 /api/{module} 动态段抢先匹配。
ROUTERS = [dispatch, cross_module, resource]
