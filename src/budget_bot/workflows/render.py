"""Deterministic fallback until the parent injects the W5 renderers."""
from budget_bot.domain.money import format_money


def render_review(review):
    plan = review['plan']
    lines = [f"Review {review['request_id']} revision {review['revision']} (INR)"]
    for index, action in enumerate(review['actions'], 1):
        fields = ', '.join(f'{k}={action[k]}' for k in sorted(action) if k != 'type')
        lines.append(f"{index}. {action['type']}: {fields}")
    lines.extend(plan['summary'])
    snapshot = plan['snapshot']
    lines.append(f"Projected available pool: {format_money(snapshot['pool'])}")
    lines.extend(f"Projected {name}: {format_money(bucket['balance'])}"
                 for name, bucket in snapshot['buckets'].items())
    lines.append(f"Dates use {snapshot['timezone']}. Expires {review['expires_at']}.")
    lines.extend('Warning: ' + warning for warning in plan['warnings'])
    lines.append('Confirm, Edit, or Cancel. Nothing is recorded until Confirm.')
    return '\n'.join(lines)


def render_result(result):
    if result['status'] != 'committed':
        return 'Review cancelled. No budget changes were recorded.'
    lines = ['Budget changes recorded.', *result['summary']]
    snapshot = result['snapshot']
    lines.append(f"Available pool: {format_money(snapshot['pool'])}")
    lines.extend(f"{name} remaining: {format_money(bucket['balance'])}"
                 for name, bucket in snapshot['buckets'].items())
    lines.extend('Warning: ' + warning for warning in result['warnings'])
    return '\n'.join(lines)
