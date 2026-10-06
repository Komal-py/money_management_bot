from test_controller import NOW, Store, Workflow, message
import pytest


@pytest.mark.asyncio
async def test_real_command_parser_defaults_to_proposal():
    from budget_bot.controller import BudgetController
    workflow = Workflow()
    controller = BudgetController(Store(), workflow, None, None, None)
    response = await controller.handle(message(text='/income 100 Salary'), NOW)
    assert response[0]['text'] == 'Review required'
    assert workflow.calls[0][2][0]['type'] == 'income'


@pytest.mark.asyncio
async def test_bad_command_gets_actionable_reply_not_durable_retry():
    from budget_bot.controller import BudgetController
    workflow = Workflow()
    controller = BudgetController(Store(), workflow, None, None, None)
    response = await controller.handle(message(text='/expense 20'), NOW)
    assert 'supply' in response[0]['text'].lower()
    assert workflow.calls == []


@pytest.mark.asyncio
async def test_help_does_not_use_model():
    from budget_bot.controller import BudgetController
    workflow = Workflow()
    controller = BudgetController(Store(), workflow, None, None, None)
    response = await controller.handle(message(text='/help'), NOW)
    assert '/expense' in response[0]['text']
    assert workflow.calls == []
