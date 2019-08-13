from . import invoke_rpc
from . import ProcessGroupAgent

import array
import sys
import torch


_agent = None


def _check_initialized():
    if _agent is None:
        raise RuntimeError("RPC has not been initialized. "
                           "Call init_rpc(name) first.")


def join_rpc():
    r"""
    Block until all local and remote RPC processes reach this method, process
    (send and receive) all pending messages, and then destroy local RPC agent.
    Every RPC process must call this method before exit.
    """
    global _agent

    if _agent:
        _agent.join()
        _agent = None


def sync_rpc():
    r"""
    Block until all local and remote RPC processes reach this method and finish
    sending all pending RPCs. As this method synchronizes at the process
    level, if multiple threads are spawned, only one of them should call this
    method at a time.
    """
    _check_initialized()
    _agent.sync()


# TODO: add a context managet to wrap init_rpc and join_rpc
def init_rpc(name, backend='pg'):
    r"""
    Initialize the local RPC agent which immediately makes the current process
    ready to send and receive RPCs. The caller needs to make sure the specified
    backend is properly intialized before calling this method. For example, to
    use ``pg`` (ProcessGroup) backend, ``init_process_group`` must be invoked
    prior to this method.

    Arguments:
        name (str): a globally unique name of the local RPC agent. (e.g.,
                    ``Trainer3``, ``ParameterServer2``, ``Master``, ``Worker1``)
        backend (str): type of RPC backend implementation. Currently,
                       process group backend ``"pg"`` is the only available
                       backend implementation. (default: ``"pg"``).
    """
    global _agent

    if _agent:
        raise RuntimeError("RPC is already initialized")

    if backend == 'pg':
        from .distributed_c10d import _get_default_group
        group = _get_default_group()
        _agent = ProcessGroupAgent(name, group)
    else:
        raise RuntimeError("Unrecognized RPC backend ", backend)


def get_id(workerName=None):
    _check_initialized()
    if workerName:
        return _agent.get_worker_id(workerName)
    else:
        return _agent.get_id()


def rpc(to, func, args=None, kwargs=None, async_call=False):
    r"""
    Make an RPC call to run function ``func`` on worker ``to``. By default, it
    blocks until the return value is locally available. RPC messages are sent
    and received in parallel to execution of Python code. This method is
    thread-safe.

    Arguments:
        to (int or str): id or name of the destination worker.
        func (callable): a builtin function (e.g., ``torch.add``).
        args (tuple): the argument tuple for the ``func`` invocation.
        kwargs (dict): is a dictionary of keyword arguments for the ``func``
                       invocation.
        async_call (bool): If set to ``True``, this will be an asynchronous RPC,
                           and returns a ``torch.distributed.FutureMessage``
                           object immediately. Otherwise, this RPC will block
                           until the return value is locally available.
                           (default: ``False``)

    Returns:
        If ``async_call`` is ``False``, returns the result of running ``func``
        on ``args`` and ``kwargs``. If ``async_call`` is ``True``, returns a
        ``torch.distributed.FutureMessage`` object that can be waited on. When
        completed, the return value of ``func`` on ``args`` and ``kwargs`` can
        be retrieved from the ``FutureMessage`` object.

    Example::

        Synchronous example:

        On worker 0:
        >>> import torch.distributed as dist
        >>> dist.init_process_group(backend='gloo', rank=0, world_size=2)
        >>> dist.init_rpc("worker0")
        >>> ret = dist.rpc("worker1", torch.add, args=(torch.ones(2), 3))
        >>> dist.join_rpc()

        One worker 1:
        >>> import torch.distributed as dist
        >>> dist.init_process_group(backend='gloo', rank=1, world_size=2)
        >>> dist.init_rpc("worker1")
        >>> dist.join_rpc()

        Asynchronous example:

        On worker 0:
        >>> import torch.distributed as dist
        >>> dist.init_process_group(backend='gloo', rank=0, world_size=2)
        >>> dist.init_rpc("worker0")
        >>> fut1 = dist.rpc("worker1", torch.add, args=(torch.ones(2), 3), async_call=True)
        >>> fut2 = dist.rpc("worker1", torch.add, args=(torch.ones(2), 2), async_call=True)
        >>> result = fut1.wait() + fut2.wait()
        >>> dist.join_rpc()

        One worker 1:
        >>> import torch.distributed as dist
        >>> dist.init_process_group(backend='gloo', rank=1, world_size=2)
        >>> dist.init_rpc("worker1")
        >>> dist.join_rpc()
    """
    _check_initialized()

    qualified_name = torch.jit._find_builtin(func)
    if qualified_name is None:
        raise RuntimeError("unknown builtin function %s." % func)

    args = args if args else ()
    kwargs = kwargs if kwargs else {}
    if isinstance(to, str):
        to = _agent.get_worker_id(to)
    fut = invoke_rpc(_agent, to, qualified_name, *args, **kwargs)

    if async_call:
        return fut
    else:
        return fut.wait()
