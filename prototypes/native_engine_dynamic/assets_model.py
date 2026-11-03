"""Ordinary managed model using declared bundles and BackendContext handles."""

import sys
import sysconfig

from simulatte import model
from simulatte.execution.native_assets import NativeAssetBundle
from .execution_adapter import ExecutionAdapter, Parameters, templates
from .execution_model import verify_assets

PREFIX = "prototypes/native_engine_dynamic/.build/"
ENGINE = NativeAssetBundle("engine", (
    PREFIX + "_nd_scalar" + sysconfig.get_config_var("EXT_SUFFIX"),
    PREFIX + ("dynamic_engine.dylib" if sys.platform == "darwin" else "dynamic_engine.so"),
))


@model(params=Parameters, horizon=30000, native_assets=(ENGINE,))
def build(env, layout, params):
    verify_assets()
    ExecutionAdapter(env, layout, params, templates(env, params))
