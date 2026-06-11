# /services/state.py

import time

class EngineState:
    """Holds the real-time status for a single user/session."""
    def __init__(self):
        self.current_status = "AWAITING QUERY..."
        self.progress_percent = 0
        self.last_updated = time.time()

    def update(self, status: str, progress: int = 0):
        self.current_status = status
        self.progress_percent = progress
        self.last_updated = time.time()

    def reset(self):
        self.current_status = "AWAITING QUERY..."
        self.progress_percent = 0
        self.last_updated = time.time()

class StateManager:
    """
    Manages multiple engine states mapped by client_id. 
    Prevents concurrent users from overwriting each other's loading screens.
    """
    def __init__(self):
        self._states: dict[str, EngineState] = {}

    def get_state(self, client_id: str = "default") -> EngineState:
        """Retrieves or creates a state for a specific client."""
        if client_id not in self._states:
            self._states[client_id] = EngineState()
        return self._states[client_id]

    def update(self, status: str, progress: int = 0, client_id: str = "default"):
        """Convenience method to safely update a specific client's state."""
        state = self.get_state(client_id)
        state.update(status, progress)
        self._clean_stale_states()

    def _clean_stale_states(self, max_age_seconds: int = 3600):
        """Internal cleanup to prevent memory leaks from abandoned sessions."""
        current_time = time.time()
        stale_keys = [k for k, v in self._states.items() if (current_time - v.last_updated) > max_age_seconds]
        for k in stale_keys:
            del self._states[k]

    # --- Backwards Compatibility Wrappers ---
    # These ensure the app doesn't crash before we update main.py and data_engine.py
    
    @property
    def current_status(self) -> str:
        return self.get_state("default").current_status

    @property
    def progress_percent(self) -> int:
        return self.get_state("default").progress_percent

# Export the Global Session Manager
cascade_state = StateManager()