from django.db import models
from clinics.models import Clinic
from accounts.models import SupportUser
from django.db.models.signals import post_save
from django.dispatch import receiver


class Ticket(models.Model):
    """Support ticket — problema reportado por clínica ou admin"""

    STATUS = [
        ('open', 'Aberto'),
        ('in_progress', 'Em Progresso'),
        ('resolved', 'Resolvido'),
        ('closed', 'Fechado'),
    ]

    PRIORITY = [
        ('low', 'Baixa'),
        ('medium', 'Média'),
        ('high', 'Alta'),
        ('critical', 'Crítica'),
    ]

    clinic = models.ForeignKey(
        Clinic,
        on_delete=models.CASCADE,
        related_name='support_tickets',
        help_text='Clínica que reportou o problema'
    )
    created_by = models.ForeignKey(
        SupportUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_tickets',
        help_text='Quem criou o ticket'
    )
    assigned_to = models.ForeignKey(
        SupportUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tickets',
        help_text='Quem está resolvendo'
    )

    title = models.CharField(max_length=255, help_text='Título do problema')
    description = models.TextField(help_text='Descrição detalhada')
    status = models.CharField(
        max_length=20,
        choices=STATUS,
        default='open',
        help_text='Estado atual'
    )
    priority = models.CharField(
        max_length=20,
        choices=PRIORITY,
        default='medium',
        help_text='Nível de urgência'
    )

    notion_page_id = models.CharField(
        max_length=255,
        blank=True,
        help_text='UUID da página no Notion (para sincronização)'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Data/hora que foi resolvido'
    )

    class Meta:
        db_table = 'support_ticket'
        ordering = ['-created_at']
        verbose_name = 'Ticket de Suporte'
        verbose_name_plural = 'Tickets de Suporte'
        indexes = [
            models.Index(fields=['clinic', '-created_at']),
            models.Index(fields=['status', '-priority']),
            models.Index(fields=['assigned_to', 'status']),
        ]
        permissions = [
            ('can_assign_ticket', 'Pode atribuir tickets a membros'),
            ('can_resolve_ticket', 'Pode marcar ticket como resolvido'),
        ]

    def __str__(self):
        return f'[{self.get_priority_display()}] {self.title} ({self.get_status_display()})'

    def is_open(self):
        """Verifica se ticket está aberto"""
        return self.status in ['open', 'in_progress']


class TicketMessage(models.Model):
    """Mensagem/comentário em um ticket"""

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name='messages',
        help_text='Ticket ao qual a mensagem pertence'
    )
    author = models.ForeignKey(
        SupportUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='ticket_messages',
        help_text='Autor da mensagem'
    )
    message = models.TextField(help_text='Conteúdo da mensagem')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'support_ticket_message'
        ordering = ['created_at']
        verbose_name = 'Mensagem de Ticket'
        verbose_name_plural = 'Mensagens de Ticket'
        indexes = [
            models.Index(fields=['ticket', 'created_at']),
        ]

    def __str__(self):
        return f'{self.author.username if self.author else "Deletado"} — {self.created_at:%Y-%m-%d %H:%M}'


@receiver(post_save, sender=Ticket)
def sync_ticket_to_notion(sender, instance, created, **kwargs):
    """
    Quando um ticket é criado ou atualizado, enfileira a sincronização com Notion
    de forma assíncrona via Celery, evitando bloquear a thread HTTP.
    """
    from .tasks import sync_ticket_to_notion as task
    task.delay(str(instance.pk))