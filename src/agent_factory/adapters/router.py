"""Routes each agent call to the runtime its role is bound to (models.yaml), and every later
poll/cancel to the runtime that started it (recorded in the handle, so it survives a restart)."""

from __future__ import annotations

from .protocol import AgentRequest, AgentRuntime, CancelResult, ResumeRequest, RunHandle, RunSnapshot, RuntimeCapabilities


class RuntimeRouter:
    def __init__(self, runtimes: dict[str, AgentRuntime]):
        if "claude" not in runtimes:
            raise ValueError("the claude runtime is required")
        self.runtimes = runtimes

    def _for(self, name: str) -> AgentRuntime:
        runtime = self.runtimes.get(name)
        if runtime is None:
            raise RuntimeError(f"runtime {name!r} is not configured (models.yaml `runtimes`)")
        return runtime

    def _of(self, handle: RunHandle) -> AgentRuntime:
        return self._for(handle.meta.get("runtime", "claude"))  # handles from before routing were all claude

    async def capabilities(self) -> RuntimeCapabilities:
        return await self.runtimes["claude"].capabilities()

    async def start(self, request: AgentRequest) -> RunHandle:
        handle = await self._for(request.runtime).start(request)
        handle.meta.setdefault("runtime", request.runtime)
        return handle

    async def poll(self, handle: RunHandle) -> RunSnapshot:
        return await self._of(handle).poll(handle)

    async def cancel(self, handle: RunHandle) -> CancelResult:
        return await self._of(handle).cancel(handle)

    async def resume(self, handle: RunHandle, request: ResumeRequest) -> RunHandle:
        return await self._of(handle).resume(handle, request)
