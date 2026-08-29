# CozoScript for facto

Enough Datalog to write your own rules over the ledger. Read this before
`facto.py query`; the syntax is small but unforgiving about a few things.

## The stored relations

```
source    {id => title, uri, path, sha, added_at}
predicate {name => description, functional, object_type, declared_at}
proposal  {name => rationale, quote, source_id, proposed_at}
fact      {id => pred, subject, object, source_id, quote,
                 asserted_at, retracted_at, retract_reason}
```

Stored relations are referenced with a leading `*`. Fields are matched by name,
not position, and you only name the ones you want:

```
?[subject, object] := *fact{pred: 'sample_size', subject, object}
```

`subject` bare means "bind the field `subject` to a variable of the same name".
`pred: 'sample_size'` means "constrain the field `pred` to this literal".
`object: obj` binds the field to a differently named variable.

**A live fact is one with `retracted_at: ''`.** Nothing enforces this for you.
Leaving it off is the single most common bug in a hand-written rule: the answer
silently includes claims you retracted months ago.

## Rules

A query is a set of rules; the one named `?` is the output.

```
described[s] := *fact{subject: s, retracted_at: ''}
?[entity] := *fact{object: entity, retracted_at: ''}, not described[entity]
```

Comma is conjunction. Multiple rules with the same head are a union (or):

```
touched[s] := *fact{subject: s, retracted_at: ''}
touched[s] := *fact{object: s, retracted_at: ''}
```

Negation with `not` requires every variable in the negated atom to be bound by
a positive atom earlier in the same rule body, and `not` over a stored relation
is best written against a helper rule as above.

## Joins

Repeating a variable joins on it:

```
?[a, b, c] :=
    *fact{pred: 'cites', subject: a, object: b, retracted_at: ''},
    *fact{pred: 'cites', subject: b, object: c, retracted_at: ''}
```

## Recursion

Rules may refer to themselves; Cozo runs them to a fixpoint. Citation reach:

```
reaches[a, b] := *fact{pred: 'cites', subject: a, object: b, retracted_at: ''}
reaches[a, c] := reaches[a, b], *fact{pred: 'cites', subject: b, object: c, retracted_at: ''}
?[b] := reaches['rosen_2019', b]
```

## Aggregation

Aggregates go in the rule head; the non-aggregated head variables are the
grouping key. Because Datalog rules are sets, duplicate rows collapse before
counting — which is exactly why `srcs` below counts *distinct* sources:

```
srcs[subject, pred, source_id] := *fact{pred, subject, source_id, retracted_at: ''}
counted[subject, pred, count(source_id)] := srcs[subject, pred, source_id]
?[subject, pred, n] := counted[subject, pred, n], n > 1
```

Others: `sum`, `min`, `max`, `mean`, `collect` (gathers into a list).

## Filters, ordering, limits

```
?[subject, object] := *fact{pred: 'sample_size', subject, object, retracted_at: ''},
                      n = to_int(object), n > 100
    :order -object
    :limit 20
```

`object` is stored as a String, so numeric comparison needs `to_int` or
`to_float`. Useful builtins: `str_includes`, `lowercase`, `starts_with`,
`length`, `concat`.

## Parameters

`facto.py query` passes no parameters, but rules inside `store.py` can:

```python
query(db, "?[object] := *fact{pred: $p, subject: $s, object}", {"p": "sample_size", "s": "rosen_2019"})
```

Interpolating values into the script string instead will eventually break on a
quote character in a title. Use `$params`.

## Worked example: a coverage matrix

"Which of my subjects am I missing a sample size for?" — the query that turns a
research question into a reading list.

```
studies[s] := *fact{pred: 'is_study', subject: s, retracted_at: ''}
sized[s]   := *fact{pred: 'sample_size', subject: s, retracted_at: ''}
?[s] := studies[s], not sized[s]
```

## Worked example: disagreement between sources

Two sources that contradict each other anywhere — a reviewer-reliability signal
the built-in per-fact conflict rule cannot see:

```
disagree[s1, s2] :=
    *predicate{name: pred, functional: true},
    *fact{pred, subject, object: o1, source_id: s1, retracted_at: ''},
    *fact{pred, subject, object: o2, source_id: s2, retracted_at: ''},
    o1 != o2
tally[s1, s2, count(subject)] :=
    disagree[s1, s2], *fact{subject, source_id: s1, retracted_at: ''}
?[t1, t2, n] := tally[s1, s2, n], *source{id: s1, title: t1}, *source{id: s2, title: t2}
    :order -n
```

## Promoting a rule

When a rule earns its keep, move it into `scripts/store.py` beside `CONFLICTS`
and call it from `cmd_check` in `scripts/facto.py`. A rule that only runs when
someone remembers to type it is a rule that stops running.

Full language reference: https://docs.cozodb.org/en/latest/queries.html
