"""Untrusted interpretation proposals, never backend financial authority."""
from typing import Annotated, Literal
from datetime import date

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


def _nonblank(value):
    if not value.strip():
        raise ValueError('Text must not be blank')
    return value


def _date_expression(value):
    if value not in {'today', 'yesterday'}:
        date.fromisoformat(value)
    return value

Money = Annotated[str, Field(pattern=r'^\d+(?:\.\d{1,2})?$')]
Name = Annotated[str, Field(min_length=1, max_length=60), AfterValidator(_nonblank)]
Description = Annotated[str, Field(min_length=1, max_length=240), AfterValidator(_nonblank)]
DateExpression = Annotated[str, Field(pattern=r'^(today|yesterday|[0-9]{4}-[0-9]{2}-[0-9]{2})$'),
                           AfterValidator(_date_expression)]
ISODate = Annotated[str, Field(pattern=r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'),
                    AfterValidator(_date_expression)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Income(StrictModel):
    type: Literal['income']
    amount_inr: Money
    description: Description
    date_expression: DateExpression | None = None


class Allocate(StrictModel):
    type: Literal['allocate']
    amount_inr: Money
    bucket_name: Name


class Transfer(StrictModel):
    type: Literal['transfer']
    amount_inr: Money
    source_bucket: Name
    destination_bucket: Name


class Expense(StrictModel):
    type: Literal['expense']
    amount_inr: Money
    bucket_name: Name
    description: Description
    date_expression: DateExpression | None = None


class CreateBucket(StrictModel):
    type: Literal['create_bucket']
    name: Name


class Undo(StrictModel):
    type: Literal['undo']
    last: bool = False
    reference: Description | None = None

    @model_validator(mode='after')
    def selection(self):
        if self.last == bool(self.reference):
            raise ValueError('Select last or a natural-language reference')
        return self


class Changes(StrictModel):
    amount_inr: Money | None = None
    bucket_name: Name | None = None
    source_bucket: Name | None = None
    destination_bucket: Name | None = None
    description: Description | None = None
    date_expression: DateExpression | None = None

    @model_validator(mode='after')
    def nonempty(self):
        if not any(self.model_dump().values()):
            raise ValueError('Correction must specify changes')
        return self


class Correct(StrictModel):
    type: Literal['correct']
    reference: Description
    changes: Changes


class SetTarget(StrictModel):
    type: Literal['set_target']
    bucket_name: Name
    amount_inr: Money | None = None
    remove: bool = False

    @model_validator(mode='after')
    def exclusive(self):
        if self.remove == (self.amount_inr is not None):
            raise ValueError('Specify amount or remove')
        return self


Action = Income | Allocate | Transfer | Expense | CreateBucket | Undo | Correct | SetTarget


class Query(StrictModel):
    report: Literal['balances', 'spending', 'calendar']
    period: Literal['today', 'week', 'month', 'range']
    start: ISODate | None = None
    end: ISODate | None = None
    bucket_name: Name | None = None

    @model_validator(mode='after')
    def dates(self):
        if self.period == 'range':
            if self.start is None or self.end is None:
                raise ValueError('Range requires dates')
            if date.fromisoformat(self.start) > date.fromisoformat(self.end):
                raise ValueError('Reversed range')
        elif self.start is not None or self.end is not None:
            raise ValueError('Dates only for range')
        return self


class Interpretation(StrictModel):
    schema_version: Literal[1]
    kind: Literal['mutation', 'query', 'clarification', 'unsupported']
    actions: list[Action] = Field(max_length=8)
    missing_fields: list[Literal['amount_inr', 'bucket_name', 'source_bucket', 'destination_bucket',
                               'description', 'date_expression', 'funding_source', 'transaction_reference',
                               'name', 'period', 'start', 'end']] = Field(max_length=12)
    clarification_question: str | None
    query: Query | None

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int:
            raise ValueError('Version must be integer')
        return value

    @model_validator(mode='after')
    def consistent(self):
        if type(self.schema_version) is not int:
            raise ValueError('Version must be integer')
        if self.kind == 'mutation':
            if not self.actions or self.query or self.missing_fields or self.clarification_question:
                raise ValueError('Invalid mutation envelope')
        elif self.kind == 'query':
            if not self.query or self.actions or self.missing_fields or self.clarification_question:
                raise ValueError('Invalid query envelope')
        else:
            if self.actions or self.query:
                raise ValueError('Non-action envelope has actions')
            if self.kind == 'clarification' and not self.missing_fields:
                raise ValueError('Missing supported clarification fields')
        return self


def interpretation_schema():
    """Responses strict mode requires all properties, even nullable ones."""
    schema = Interpretation.model_json_schema()

    def visit(node):
        if isinstance(node, dict):
            node.pop('default', None)
            if node.get('type') == 'object':
                node['required'] = list(node['properties'])
                node['additionalProperties'] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(schema)
    return schema
