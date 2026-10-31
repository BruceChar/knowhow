"""Role templates and permission helpers."""
from __future__ import annotations

PERMISSIONS = {
    "ask.use": "使用销售问答",
    "knowledge.view": "查看知识",
    "knowledge.suggest": "提交知识草稿",
    "knowledge.edit": "编辑知识",
    "knowledge.publish": "发布知识",
    "gap.manage": "处理知识缺口",
    "dashboard.view": "查看经营看板",
    "team.manage": "管理团队成员",
    "role.manage": "管理角色权限",
    "data.all": "查看全部团队数据",
    "training.use": "使用新人训练",
    "facts.view": "查看产品与报价事实",
    "facts.manage": "导入和管理产品报价",
}

ROLE_TEMPLATES = {
    "owner": {
        "name": "老板",
        "permissions": list(PERMISSIONS),
    },
    "admin": {
        "name": "管理员",
        "permissions": list(PERMISSIONS),
    },
    "steward": {
        "name": "知识负责人",
        "permissions": [
            "ask.use", "knowledge.view", "knowledge.suggest",
            "knowledge.edit", "knowledge.publish", "gap.manage",
            "dashboard.view", "data.all",
            "training.use",
            "facts.view", "facts.manage",
        ],
    },
    "sales": {
        "name": "业务员",
        "permissions": [
            "ask.use", "knowledge.view", "knowledge.suggest", "training.use",
            "facts.view",
        ],
    },
}


def valid_permissions(values: list[str]) -> list[str]:
    return sorted(set(values) & set(PERMISSIONS))

