from django import template

register = template.Library()

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


@register.filter
def money(value, currency="USD"):
    if value is None:
        return ""
    amount = value / 100
    symbol = CURRENCY_SYMBOLS.get(currency)
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {currency}"
