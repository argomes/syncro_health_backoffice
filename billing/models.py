import uuid
from django.db import models
from clinics.models import Clinic


class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=50, unique=True)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    max_users = models.PositiveIntegerField()
    max_professionals = models.PositiveIntegerField()
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['price']
        verbose_name = 'Plano'
        verbose_name_plural = 'Planos'

    def __str__(self):
        return f'{self.name} (R$ {self.price})'


class InvoiceStatus(models.TextChoices):
    PENDING = 'pending', 'Pendente'
    PAID = 'paid', 'Paga'
    OVERDUE = 'overdue', 'Vencida'
    CANCELLED = 'cancelled', 'Cancelada'


class Invoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    clinic = models.ForeignKey(Clinic, on_delete=models.PROTECT, related_name='invoices')
    # Competência no formato YYYY-MM (ex: 2026-06)
    competencia = models.CharField(max_length=7)
    amount = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=20, choices=InvoiceStatus, default=InvoiceStatus.PENDING)
    due_date = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-competencia']
        unique_together = [('clinic', 'competencia')]
        verbose_name = 'Fatura'
        verbose_name_plural = 'Faturas'

    def __str__(self):
        return f'{self.clinic.name} — {self.competencia} ({self.status})'
