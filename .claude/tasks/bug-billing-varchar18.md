# BUG — `billing` test suite: DataError varchar(18)

**Encontrado em:** QA review da TASK-BO-R07 (2026-10-01), pré-existente, não relacionado à mudança de namespace.

`python manage.py test billing` falha com 19 erros:
```
django.db.utils.DataError: value too long for type character varying(18)
```
Ocorre dentro das factories de teste que criam `Clinic` (`db_name=f'db_{uuid.uuid4().hex[:8]}'`, possivelmente `name`/`slug`) — algum campo com `max_length` de 18 está recebendo valor maior gerado pelos dados de teste.

Confirmado em baseline (`git stash` antes de qualquer mudança do R07) — não é regressão desta ou de nenhuma task recente mapeada. Precisa de investigação separada: localizar qual campo tem `max_length=18` no model `Clinic` (ou relacionado) e por que o dado de teste estoura isso.

**status:** aberto, não bloqueante, não atribuído.
