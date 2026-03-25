"""Storage Router — DB path abstraction for future multi-tenant support."""

import os

NEXO_HOME = os.environ.get("NEXO_HOME", os.path.expanduser("~/.nexo"))


class StorageRouter:
    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id

    def nexo_db_path(self) -> str:
        if self.tenant_id == "default":
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "nexo.db")
        return os.path.join(NEXO_HOME, "tenants", self.tenant_id, "nexo.db")

    def cognitive_db_path(self) -> str:
        if self.tenant_id == "default":
            return os.path.join(os.path.dirname(os.path.abspath(__file__)), "cognitive.db")
        return os.path.join(NEXO_HOME, "tenants", self.tenant_id, "cognitive.db")


_default_router = StorageRouter("default")

def get_router(tenant_id: str = "default") -> StorageRouter:
    if tenant_id == "default":
        return _default_router
    return StorageRouter(tenant_id)
