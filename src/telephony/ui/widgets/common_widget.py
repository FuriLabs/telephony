# Copyright (C) 2026 alaraajavamma aki@urheiluaki.fi
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from ...backend.utils.thread_utils import run_in_background

from gi.repository import GLib
from loguru import logger


class DataLoader:
    """Helper for loading data asynchronously in chunks to prevent UI blocking."""

    @staticmethod
    def load_data(fetch_func, model_add_func, model, check_token_func=None, on_finish=None, clear_on_first_chunk=True):
        """Execute data loading task."""
        def task():
            try:
                if check_token_func and not check_token_func():
                    return
                processed_items = fetch_func()

                if not processed_items:
                    def update_empty():
                        if check_token_func and not check_token_func():
                            return False
                        if clear_on_first_chunk:
                            model.remove_all()
                        if model_add_func:
                            model_add_func(model, [])
                        return False
                    GLib.idle_add(update_empty)
                    if on_finish and callable(on_finish):
                        GLib.idle_add(on_finish)
                    return

                chunk_size = 20
                state = {'idx': 0, 'is_first_chunk': True}

                def process_next_chunk():
                    if check_token_func and not check_token_func():
                        return False

                    idx = state['idx']
                    if idx >= len(processed_items):
                        if on_finish and callable(on_finish):
                            on_finish()
                        return False

                    if state['is_first_chunk'] and clear_on_first_chunk:
                        model.remove_all()

                    chunk = processed_items[idx:idx + chunk_size]
                    if chunk:
                        model_add_func(model, chunk)

                    state['is_first_chunk'] = False
                    state['idx'] += chunk_size
                    return True

                GLib.idle_add(process_next_chunk)
            except Exception as e:
                logger.error(f"Data loading error: {e}")
        run_in_background(task)
