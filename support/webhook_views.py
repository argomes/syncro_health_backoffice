"""
Webhook de VOLTA do Zoho Desk (BACFF-AVULSA-10).

Contexto: BACFF-AVULSA-07 criou a sincronização de IDA — quando um Ticket é
criado/atualizado no Backoffice, um signal (`support/models.py::
sync_ticket_to_zoho`) empurra a mudança pro Zoho Desk via `ZohoDeskService`.
Não existe (e nunca existiu) o caminho inverso: se o time de suporte responde
direto no painel do Zoho Desk — fluxo natural, já que é lá que a equipe
trabalha — a resposta nunca aparecia como `TicketMessage` local. Uma tela de
consulta pro admin da clínica (BACFF-AVULSA-09, ainda não implementada)
mostraria o ticket "aberto pra sempre" sem nunca ver a resposta.

Este módulo resolve só isso: recebe o webhook do evento "Add Comment" do
Zoho Desk e cria o `TicketMessage` correspondente localmente.

--------------------------------------------------------------------------
CONFIRMAÇÃO DE DOCUMENTAÇÃO / SUPOSIÇÃO DE PAYLOAD (leia antes de configurar)
--------------------------------------------------------------------------
Confirmado via busca (help.zoho.com/portal/.../setting-up-webhooks-in-desk e
zoho.com/desk/extensions/guide/webhooks.html): o Zoho Desk permite configurar
Webhooks em "Setup > Automation > Webhooks", com eventos por módulo. Para o
módulo Ticket existe o evento de comentário — internamente referenciado como
`ticket_comment.add` — disparado quando um comentário é adicionado a um
ticket (inclusive pelo agente de suporte no painel).

O que NÃO dá pra confirmar sem acesso ao painel real: Zoho Desk webhooks
customizados usam um corpo de requisição (URL params ou JSON body) que VOCÊ
define no momento da configuração, usando "merge fields" (placeholders tipo
`${Ticket.id}`, `${Comment.content}`) — não existe um payload fixo único
"de fábrica" pra este evento (ao contrário do webhook do ASAAS, que manda um
formato fixo). Ou seja: não é uma suposição arriscada, é a própria mecânica
do Zoho Desk — quem define o payload é quem configura o webhook.

Por isso este endpoint espera um corpo JSON com este formato exato, que você
deve montar no painel do Zoho Desk usando os merge fields indicados:

    {
        "ticket_id": "${Ticket.id}",
        "comment": "${Comment.content}",
        "author_name": "${Comment.commenterName}",
        "is_public": ${Comment.isPublic}
    }

Campos obrigatórios: `ticket_id`, `comment`. Os demais (`author_name`,
`is_public`) são opcionais — se ausentes, a mensagem é salva sem prefixo de
autor. `is_public` não bloqueia a criação da mensagem (guardamos tanto
comentários públicos quanto privados/internos do Zoho como histórico local);
é aceito no payload apenas para uso futuro caso se decida filtrar.

⚠️ Os NOMES EXATOS dos merge fields acima (`Comment.commenterName`,
`Comment.isPublic` etc.) não foram confirmados letra-por-letra contra a
lista de merge fields disponível no painel (isso só é visível dentro do
editor de Webhooks do Zoho, autenticado, que não temos acesso aqui). Ao
configurar o webhook de verdade, ANTES de ativar em produção: abra a tela
"Adicionar Webhook" no painel, filtre os merge fields disponíveis para o
módulo Ticket/Comment, e ajuste os nomes no template JSON acima se os reais
forem diferentes. O código abaixo depende só das CHAVES do JSON recebido
(`ticket_id`, `comment`, `author_name`, `is_public`), não dos nomes dos
merge fields do Zoho — então o ajuste, se necessário, é só no painel deles,
sem tocar neste arquivo.

--------------------------------------------------------------------------
PASSO A PASSO MANUAL NO PAINEL DO ZOHO DESK (ação do usuário, não automatizável)
--------------------------------------------------------------------------
1. No Zoho Desk, ir em Setup (ícone de engrenagem) > Automation > Webhooks.
2. Clicar em "+ New Webhook" (ou "Configure Webhook").
3. Nome: algo como "Backoffice - Comentário no Ticket".
4. Module: Tickets.
5. URL: https://<host-do-backoffice>/api/support/webhooks/zoho-comment/
   (mesmo domínio já usado pelas outras integrações do Backoffice).
6. Method: POST.
7. Headers: adicionar um header customizado
       X-Webhook-Secret: <mesmo valor de ZOHO_DESK_WEBHOOK_SECRET no .env>
   (gerar um valor aleatório longo, ex.: `python -c "import secrets;
   print(secrets.token_urlsafe(32))"`, e usar o MESMO valor nos dois lados).
8. Body/Payload (JSON): colar o template acima, ajustando os nomes dos merge
   fields conforme o que o editor do Zoho oferecer para Ticket/Comment.
9. Trigger: associar este webhook a uma regra de Workflow/Automação com
   critério "Comment Added" (ou equivalente) no módulo Tickets — o Zoho Desk
   exige uma regra de automação apontando pro webhook, o webhook sozinho não
   dispara nada.
10. Salvar, ativar a regra, e testar respondendo um ticket real que tenha
    `zoho_ticket_id` correspondente a um Ticket existente no Backoffice.
11. Conferir nos logs do Backoffice (`logger` deste módulo) se o comentário
    chegou e o TicketMessage foi criado.
"""
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from billing.webhook_views import _check_webhook_rate_limit
from .models import Ticket, TicketMessage

logger = logging.getLogger(__name__)


@csrf_exempt
def zoho_ticket_comment_webhook(request):
    """
    POST /api/support/webhooks/zoho-comment/

    Webhook de VOLTA (Zoho Desk -> Backoffice): recebe o comentário/resposta
    que o time de suporte deu direto no Zoho Desk e cria o TicketMessage
    local correspondente, achado pelo `zoho_ticket_id`.

    Autenticação: header `X-Webhook-Secret` comparado (timing-safe) contra
    `settings.ZOHO_DESK_WEBHOOK_SECRET` — mesmo padrão do webhook do ASAAS
    (`billing/webhook_views.py::asaas_webhook`), adaptado pro header
    customizável do Zoho Desk em vez do header fixo `asaas-access-token`.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
    ip = ip.split(',')[0].strip()
    if not _check_webhook_rate_limit(ip):
        logger.warning("zoho_ticket_comment_webhook: rate limit atingido. IP=%s", ip)
        return HttpResponse(status=429)

    expected_secret = getattr(settings, 'ZOHO_DESK_WEBHOOK_SECRET', '')
    received_secret = request.headers.get('X-Webhook-Secret', '')
    if not expected_secret or not hmac.compare_digest(
        expected_secret.encode('utf-8'),
        received_secret.encode('utf-8'),
    ):
        logger.warning(
            "zoho_ticket_comment_webhook: secret inválido ou ausente. IP=%s",
            request.META.get('REMOTE_ADDR'),
        )
        return HttpResponse(status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    zoho_ticket_id = str(data.get('ticket_id') or '').strip()
    comment = data.get('comment')
    author_name = data.get('author_name')

    # Não logamos `comment` (pode conter dado de paciente/PHI relatado no
    # ticket) nem `author_name` — só IDs e contadores, igual ao restante do
    # projeto (regra 4.1 do CLAUDE.md: nunca logar PHI).
    if not zoho_ticket_id or not comment:
        logger.warning(
            "zoho_ticket_comment_webhook: payload incompleto (faltando ticket_id ou comment)."
        )
        # 200 mesmo em payload malformado: é uma automação do Zoho, não um
        # cliente HTTP que vá corrigir o pedido — retornar erro só garante
        # retry infinito de um payload que nunca vai ficar válido sozinho.
        return JsonResponse({'status': 'ignored_invalid_payload'}, status=200)

    # BO-SEC-010 (achado de segurança na revisão de BACFF-AVULSA-10): há uma
    # UniqueConstraint em nível de banco garantindo zoho_ticket_id único
    # (fora de string vazia) desde a migração que acompanha esta mudança,
    # mas checamos count() > 1 aqui como defesa em profundidade — se por
    # algum motivo (ex.: dado que já existia antes da migração ser aplicada
    # em produção) houver duplicata, preferimos falhar visivelmente a
    # escrever silenciosamente na clínica errada (cross-tenant PHI leak).
    matching_tickets = list(Ticket.objects.filter(zoho_ticket_id=zoho_ticket_id)[:2])
    if len(matching_tickets) > 1:
        logger.error(
            "zoho_ticket_comment_webhook: zoho_ticket_id=%s corresponde a mais de um Ticket local — "
            "recusando processar para não escrever na clínica errada.",
            zoho_ticket_id,
        )
        return JsonResponse({'status': 'ignored_ambiguous_ticket_id'}, status=200)

    ticket = matching_tickets[0] if matching_tickets else None
    if not ticket:
        logger.warning(
            "zoho_ticket_comment_webhook: nenhum Ticket local com zoho_ticket_id=%s",
            zoho_ticket_id,
        )
        # 200 de propósito (mesmo espírito do asaas_webhook): não é erro do
        # lado do Zoho, e não queremos que ele fique re-tentando pra sempre
        # um ticket que o Backoffice nunca vai reconhecer.
        return JsonResponse({'status': 'ignored_ticket_not_found'}, status=200)

    message_text = f'[Zoho Desk] {author_name}: {comment}' if author_name else f'[Zoho Desk] {comment}'
    # author=None é o mesmo padrão já usado pra mensagens que não vêm de um
    # SupportUser local (ex.: Edge Gateway criando ticket) — não criamos um
    # campo `source`/`is_from_support` novo porque o prefixo "[Zoho Desk]"
    # na própria mensagem já resolve a distinção visual sem migração extra.
    try:
        ticket_message = TicketMessage.objects.create(ticket=ticket, author=None, message=message_text)
    except Exception as exc:
        # Nunca logar `message_text`/`comment` aqui — pode conter PHI citada
        # pela clínica na resposta. Só o tipo da exceção e IDs. Retorna 200
        # (não 500) pra não vazar payload/stack trace numa página de debug
        # do Django caso DEBUG esteja ligado por engano em produção, e pra
        # não disparar retry infinito do Zoho num erro que reprocessar não resolve.
        logger.error(
            "zoho_ticket_comment_webhook: falha ao criar TicketMessage para ticket %s (zoho_ticket_id=%s): %s",
            ticket.id,
            zoho_ticket_id,
            type(exc).__name__,
        )
        return JsonResponse({'status': 'error_suppressed'}, status=200)

    logger.info(
        "zoho_ticket_comment_webhook: TicketMessage %s criado para ticket local %s (zoho_ticket_id=%s)",
        ticket_message.id,
        ticket.id,
        zoho_ticket_id,
    )
    return JsonResponse({'status': 'ok', 'ticket_message_id': ticket_message.id}, status=200)
