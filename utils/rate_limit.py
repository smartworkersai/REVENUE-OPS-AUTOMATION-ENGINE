"""
Log-normal delay distribution for human-like pacing.

Human browsing follows a log-normal distribution — not uniform sleep().
Fixed 2-6s delays are mechanically detectable by bot-detection systems.
"""

import time
import numpy as np


def human_delay(mu: float = 1.2, sigma: float = 0.5, min_s: float = 0.8, max_s: float = 12.0) -> None:
    """
    Sleep for a log-normally distributed duration.

    Default params produce delays centred around ~3.3s with realistic variance.
    mu and sigma are the underlying normal distribution parameters (log-space).
    """
    raw = np.random.lognormal(mean=mu, sigma=sigma)
    delay = float(np.clip(raw, min_s, max_s))
    time.sleep(delay)


def short_delay() -> None:
    """Between keystrokes / micro-interactions."""
    human_delay(mu=0.3, sigma=0.4, min_s=0.05, max_s=1.5)


def page_delay() -> None:
    """After page navigation or form submission."""
    human_delay(mu=1.4, sigma=0.5, min_s=1.5, max_s=15.0)


def think_delay() -> None:
    """Simulates reading / decision pause."""
    human_delay(mu=1.8, sigma=0.6, min_s=2.0, max_s=20.0)
