import datetime
import re
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models

from .providers import UF_BY_IBGE_PREFIX, uf_from_ibge

IBGE_CODE_RE = re.compile(r'^\d{7}$')
UF_CHOICES: list[tuple[str, str]] = [(uf, uf) for uf in sorted(set(UF_BY_IBGE_PREFIX.values()))]


class Feriado(models.Model):
    """Feriado oficial (nacional, estadual ou municipal).

    Alimentado por dois caminhos: o cache-aside da API externa
    (`HolidayService._persist`) e o cadastro manual no portal — este
    último existe porque a API não cobre todos os municípios (ex.:
    Osasco/3534401 veio sem nenhum feriado municipal) e a clínica precisa
    completar o calendário à mão. As regras de escopo territorial ficam em
    `clean()` para valerem em qualquer formulário, não só no admin.
    """

    TYPE_NACIONAL = 'NACIONAL'
    TYPE_ESTADUAL = 'ESTADUAL'
    TYPE_MUNICIPAL = 'MUNICIPAL'
    TYPE_CHOICES = [
        (TYPE_NACIONAL, 'Nacional'),
        (TYPE_ESTADUAL, 'Estadual'),
        (TYPE_MUNICIPAL, 'Municipal'),
    ]

    date = models.DateField('data')
    name = models.CharField('nome', max_length=150)
    type = models.CharField('tipo', max_length=20, choices=TYPE_CHOICES)
    uf = models.CharField(
        'UF',
        max_length=2,
        null=True,
        blank=True,
        choices=UF_CHOICES,
        help_text=(
            'Obrigatória para feriado estadual. Para municipal é preenchida '
            'automaticamente a partir do código IBGE. Ignorada para nacional.'
        ),
    )
    ibge_code = models.CharField(
        'código IBGE do município',
        max_length=7,
        null=True,
        blank=True,
        db_index=True,
        help_text='Obrigatório para feriado municipal, 7 dígitos. Ex.: Osasco = 3534401.',
    )
    year = models.IntegerField(
        'ano',
        db_index=True,
        help_text='Derivado automaticamente da data.',
    )
    description = models.TextField('descrição', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('date', 'type', 'ibge_code')
        ordering = ['date']
        verbose_name = 'Feriado'
        verbose_name_plural = 'Feriados'

    def __str__(self) -> str:
        return f"{self.date} - {self.name} ({self.get_type_display()})"

    def _sync_year(self) -> None:
        """`year` é redundante com `date`, mas indexado e usado no filtro do
        calendário (`calendar_queryset`); nunca deve divergir da data."""
        if isinstance(self.date, datetime.date):
            self.year = self.date.year

    def _normalize_scope(self) -> None:
        """Ajusta UF/IBGE ao escopo do tipo, antes de validar.

        NACIONAL não tem escopo territorial; ESTADUAL é por UF (sem IBGE,
        como grava o service); MUNICIPAL deriva a UF do IBGE quando o
        usuário não informou — é exatamente o filtro que
        `HolidayService.calendar_queryset` aplica ao responder ao gateway.
        """
        self.ibge_code = (self.ibge_code or '').strip() or None
        self.uf = (self.uf or '').strip().upper() or None

        if self.type == self.TYPE_NACIONAL:
            self.uf = None
            self.ibge_code = None
        elif self.type == self.TYPE_ESTADUAL:
            self.ibge_code = None
        elif self.type == self.TYPE_MUNICIPAL and self.ibge_code and not self.uf:
            self.uf = uf_from_ibge(self.ibge_code)

    def clean_fields(self, exclude: Any = None) -> None:
        """Deriva `year` antes da validação de campo (roda antes de `clean`),
        senão `full_clean()` fora do admin falharia com "ano nulo"."""
        self._sync_year()
        super().clean_fields(exclude=exclude)

    def clean(self) -> None:
        """Valida escopo territorial e duplicidade conforme o tipo.

        Raises:
            ValidationError: escopo incompleto/incoerente ou feriado já
                cadastrado para a mesma data e escopo.
        """
        super().clean()
        self._sync_year()
        self._normalize_scope()

        errors: dict[str, str] = {}
        if self.type == self.TYPE_MUNICIPAL:
            if not self.ibge_code:
                errors['ibge_code'] = 'Feriado municipal exige o código IBGE do município (7 dígitos).'
            elif not IBGE_CODE_RE.match(self.ibge_code):
                errors['ibge_code'] = 'O código IBGE deve ter exatamente 7 dígitos numéricos.'
            else:
                derived_uf = uf_from_ibge(self.ibge_code)
                if derived_uf is None:
                    errors['ibge_code'] = 'Código IBGE com prefixo de UF desconhecido.'
                elif self.uf != derived_uf:
                    errors['uf'] = f'UF incoerente com o código IBGE informado (esperado: {derived_uf}).'
        elif self.type == self.TYPE_ESTADUAL and not self.uf:
            errors['uf'] = 'Feriado estadual exige a UF.'

        if errors:
            raise ValidationError(errors)

        self._validate_not_duplicated()

    def _validate_not_duplicated(self) -> None:
        """Bloqueia duplicata que a `unique_together` não pega.

        NACIONAL/ESTADUAL têm `ibge_code` NULL e NULLs são distintos em
        UNIQUE, então o banco aceitaria o mesmo feriado duas vezes e o
        endpoint do gateway o devolveria repetido. Mesmo critério de
        deduplicação de `HolidayService._persist`.
        """
        if not isinstance(self.date, datetime.date) or not self.type:
            return
        lookup: dict[str, Any] = {'type': self.type, 'date': self.date}
        if self.type == self.TYPE_MUNICIPAL:
            lookup['ibge_code'] = self.ibge_code
        elif self.type == self.TYPE_ESTADUAL:
            lookup['uf'] = self.uf
        else:
            lookup['ibge_code__isnull'] = True

        if Feriado.objects.filter(**lookup).exclude(pk=self.pk).exists():
            raise ValidationError('Já existe um feriado deste tipo cadastrado para esta data e localidade.')

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._sync_year()
        super().save(*args, **kwargs)


class FeriadoBusca(models.Model):
    """Registro de controle: "já buscamos o calendário deste ibge/ano na API".

    Por que existe: a existência de `Feriado` não serve como marcador de
    cache. Município sem feriado municipal próprio nunca tem linha com o
    seu `ibge_code`, então o critério antigo (`Feriado(ibge_code, year)
    exists`) rebatia a API paga em TODA chamada. Uma linha por par
    ibge/ano, gravada só quando a busca veio completa, resolve isso com
    uma tabela minúscula e sem tocar no schema de `Feriado`.

    Dado público (calendário oficial), sem PII e sem vínculo com tenant.
    """

    ibge_code = models.CharField(max_length=7)
    year = models.IntegerField()
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['ibge_code', 'year'], name='uniq_feriado_busca_ibge_year'),
        ]
        verbose_name = 'Busca de feriados'
        verbose_name_plural = 'Buscas de feriados'

    def __str__(self) -> str:
        return f"{self.ibge_code}/{self.year}"
