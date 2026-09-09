"""Lazy providers: importing the core does not load models or contact services."""


def create_backend(config):
    if config.backend == "demo":
        from .demo import DemoBackend
        return DemoBackend(config)
    if config.backend == "command":
        from .command import CommandBackend
        return CommandBackend(config)
    from .dashscope import DashScopeBackend
    return DashScopeBackend(config)
