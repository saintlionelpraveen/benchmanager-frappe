"""Notification configuration for Bench Manager."""


def get_notification_config():
    """Return notification configuration for Bench Manager doctypes.

    Returns:
        dict: Notification configuration dictionary.
    """
    return {
        "for_doctype": {
            "Bench Command Log": {"status": "Failed"},
        },
    }
