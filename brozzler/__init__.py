"""
brozzler/__init__.py - __init__.py for brozzler package, contains some common
code

Copyright (C) 2014-2017 Internet Archive

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from pkg_resources import get_distribution as _get_distribution
__version__ = _get_distribution('brozzler').version

class ShutdownRequested(Exception):
    pass

class NothingToClaim(Exception):
    pass

class CrawlStopped(Exception):
    pass

class ProxyError(Exception):
    pass

class ReachedLimit(Exception):
    def __init__(self, http_error=None, warcprox_meta=None, http_payload=None):
        import json
        if http_error:
            if "warcprox-meta" in http_error.headers:
                self.warcprox_meta = json.loads(
                        http_error.headers["warcprox-meta"])
            else:
                self.warcprox_meta = None
            self.http_payload = http_error.read()
        elif warcprox_meta:
            self.warcprox_meta = warcprox_meta
            self.http_payload = http_payload

    def __repr__(self):
        return "ReachedLimit(warcprox_meta=%s,http_payload=%s)" % (
                repr(self.warcprox_meta), repr(self.http_payload))

    def __str__(self):
        return self.__repr__()

# monkey-patch log level TRACE
TRACE = 5
import logging
def _logging_trace(msg, *args, **kwargs):
    logging.root.trace(msg, *args, **kwargs)
def _logger_trace(self, msg, *args, **kwargs):
    if self.isEnabledFor(TRACE):
        self._log(TRACE, msg, args, **kwargs)
logging.trace = _logging_trace
logging.Logger.trace = _logger_trace
logging._levelToName[TRACE] = 'TRACE'
logging._nameToLevel['TRACE'] = TRACE

_behaviors = None
def behaviors():
    import os, yaml, string
    global _behaviors
    if _behaviors is None:
        behaviors_yaml = os.path.join(
                os.path.dirname(__file__), 'behaviors.yaml')
        with open(behaviors_yaml) as fin:
            _behaviors = yaml.load(fin)
    return _behaviors

def behavior_script(url, template_parameters=None):
    '''
    Returns the javascript behavior string populated with template_parameters.
    '''
    import re, logging
    for behavior in behaviors():
        if re.match(behavior['url_regex'], url):
            parameters = dict()
            if 'default_parameters' in behavior:
                parameters.update(behavior['default_parameters'])
            if template_parameters:
                parameters.update(template_parameters)
            template = jinja2_environment().get_template(
                    behavior['behavior_js_template'])
            script = template.render(parameters)
            logging.info(
                    'using template=%s populated with parameters=%s for %s',
                    repr(behavior['behavior_js_template']), parameters, url)
            return script
    return None

import threading
_thread_exception_gates = {}
_thread_exception_gates_lock = threading.Lock()
def thread_accept_exceptions():
    '''
    Returns a context manager whose purpose is best explained with a snippet:

        # === thread1 ===

        # If thread2 calls `thread_raise(thread1, ...)` while do_something() is
        # executing, nothing will happen (no exception will be raised in
        # thread1).
        do_something()

        try:
            with thread_accept_exceptions():
                # Now we're in the "runtime environment" (pep340) of the
                # context manager. If thread2 calls `thread_raise(thread1,
                # ...)` while do_something_else() is running, the exception
                # will be raised here.
                do_something_else()

            # Here again if thread2 calls `thread_raise`, nothing happens.
            do_yet_another_thing()

        except:
            handle_exception()

    The context manager is reentrant, i.e. you can do this:

        with thread_accept_exceptions():
            with thread_accept_exceptions():
                blah()

            # `thread_raise` will still work here
            toot()
    '''
    class ThreadExceptionGate:
        def __init__(self):
            self.lock = threading.Lock()
            self.ok_to_raise = 0

        def __enter__(self):
            with self.lock:
                self.ok_to_raise += 1

        def __exit__(self, exc_type, exc_value, traceback):
            with self.lock:
                self.ok_to_raise -= 1
            assert self.ok_to_raise >= 0

    with _thread_exception_gates_lock:
        if not threading.current_thread().ident in _thread_exception_gates:
            _thread_exception_gates[
                    threading.current_thread().ident] = ThreadExceptionGate()
    return _thread_exception_gates[threading.current_thread().ident]

def thread_raise(thread, exctype):
    '''
    Raises the exception `exctype` in the thread `thread`, if it is willing to
    accept exceptions (see `thread_accept_exceptions`).

    Adapted from http://tomerfiliba.com/recipes/Thread2/ which explains:
    "The exception will be raised only when executing python bytecode. If your
    thread calls a native/built-in blocking function, the exception will be
    raised only when execution returns to the python code."

    Returns:
        True if exception was raised, False if `thread` is not accepting
        exceptions, or another thread is in the middle of raising an exception
        in `thread`

    Raises:
        threading.ThreadError if `thread` is not running
        TypeError if `exctype` is not a class
        ValueError, SystemError in case of unexpected problems
    '''
    import ctypes, inspect, threading, logging

    if not inspect.isclass(exctype):
        raise TypeError(
                'cannot raise %s, only exception types can be raised (not '
                'instances)' % exctype)

    if not thread.is_alive():
        raise threading.ThreadError('thread %s is not running' % thread)

    gate = _thread_exception_gates.get(thread.ident)
    if not gate:
        logging.warn(
                'thread is not accepting exceptions (gate not initialized), '
                'not raising %s in thread %s', exctype, thread)
        return False

    got_lock = gate.lock.acquire(blocking=False)
    if not got_lock:
        logging.warn(
                'could not get acquire thread exception gate lock, not '
                'raising %s in thread %s', exctype, thread)
        return False
    try:
        if not gate.ok_to_raise:
            logging.warn(
                    'thread is not accepting exceptions (gate.ok_to_raise is '
                    '%s), not raising %s in thread %s',
                    gate.ok_to_raise, exctype, thread)
            return False

        logging.info('raising %s in thread %s', exctype, thread)
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(thread.ident), ctypes.py_object(exctype))
        if res == 0:
            raise ValueError(
                    'invalid thread id? thread.ident=%s' % thread.ident)
        elif res != 1:
            # if it returns a number greater than one, you're in trouble,
            # and you should call it again with exc=NULL to revert the effect
            ctypes.pythonapi.PyThreadState_SetAsyncExc(thread.ident, 0)
            raise SystemError('PyThreadState_SetAsyncExc failed')
        return True
    finally:
        gate.lock.release()

def sleep(duration):
    '''
    Sleeps for duration seconds in increments of 0.5 seconds.

    Use this so that the sleep can be interrupted by thread_raise().
    '''
    import time
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed >= duration:
            break
        time.sleep(min(duration - elapsed, 0.5))

_jinja2_env = None
def jinja2_environment():
    global _jinja2_env
    if not _jinja2_env:
        import jinja2, json
        _jinja2_env = jinja2.Environment(
                loader=jinja2.PackageLoader('brozzler', 'js-templates'))
        _jinja2_env.filters['json'] = json.dumps
    return _jinja2_env

import urlcanon
def _remove_query(url):
    url.question_mark = b''
    url.query = b''
# XXX chop off path after last slash??
site_surt_canon = urlcanon.Canonicalizer(
        urlcanon.semantic.steps + [_remove_query])

import doublethink
import datetime
EPOCH_UTC = datetime.datetime.utcfromtimestamp(0.0).replace(
        tzinfo=doublethink.UTC)

from brozzler.worker import BrozzlerWorker
from brozzler.robots import is_permitted_by_robots
from brozzler.frontier import RethinkDbFrontier
from brozzler.browser import Browser, BrowserPool, BrowsingException
from brozzler.model import (
        new_job, new_job_file, new_site, Job, Page, Site, InvalidJobConf)
from brozzler.cli import suggest_default_chrome_exe

__all__ = ['Page', 'Site', 'BrozzlerWorker', 'is_permitted_by_robots',
           'RethinkDbFrontier', 'Browser', 'BrowserPool', 'BrowsingException',
           'new_job', 'new_site', 'Job', 'new_job_file', 'InvalidJobConf',
           'sleep', 'thread_accept_exceptions', 'thread_raise']
