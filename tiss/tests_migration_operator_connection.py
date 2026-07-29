"""
Teste da migração de dados 0011 (`.claude/tasks/TISS-MULTI-OPERATOR-STRATEGY.md`
§2 — correção do defeito de credencial duplicada).

Migra o banco de teste PARA TRÁS até o estado imediatamente anterior a
0010 (quando `TISSOperatorConfig` ainda tinha `endpoint_url`/
`gateway_provider`/`login_encrypted`/`senha_encrypted` diretamente), insere
dados que simulam o caso real do documento — uma clínica com 3
`TISSOperatorConfig` (Bradesco, Cassi, Seguros Unimed) todas apontando para
o MESMO endpoint/credencial da Orizon, e uma quarta config de uma operadora
diferente com endpoint distinto — e então roda as migrations 0010→0012 para
frente, confirmando que:

1. As 3 configs do mesmo transporte convergem para UMA `TISSOperatorConnection`
   só (a consolidação que resolve o risco de continuidade de faturamento).
2. A config de transporte diferente NÃO é misturada na mesma connection.
3. Nenhum dado de credencial é perdido (login/senha cifrados sobrevivem
   idênticos ao que estava na primeira linha do grupo).
"""
from django.db.migrations.executor import MigrationExecutor
from django.db import connection as db_connection
from django.test import TransactionTestCase

_TISS_HEAD = ('tiss', '0012_tissoperatorconfig_remove_transport_fields')


class BackfillTISSOperatorConnectionMigrationTests(TransactionTestCase):
    """
    TransactionTestCase (não TestCase): migrar schema para trás/frente faz
    DDL real, que não convive bem com o wrapper atomic per-test do TestCase.
    O `finally` sempre remigra o app `tiss` de volta ao HEAD, mesmo se uma
    asserção falhar no meio — senão o resto da suíte roda contra um schema
    antigo.
    """
    databases = {'default'}

    def _migrate(self, target):
        executor = MigrationExecutor(db_connection)
        executor.loader.build_graph()
        executor.migrate(target)
        executor.loader.build_graph()
        return executor

    def test_configs_duplicadas_do_mesmo_transporte_consolidam_em_uma_connection(self):
        try:
            self._run()
        finally:
            self._migrate([_TISS_HEAD])

    def _run(self):
        # 1. Volta ao estado logo ANTES da criação de TISSOperatorConnection.
        executor = self._migrate([('tiss', '0009_operator_call_log')])
        old_apps = executor.loader.project_state([('tiss', '0009_operator_call_log')]).apps
        Clinic = old_apps.get_model('clinics', 'Clinic')
        OldConfig = old_apps.get_model('tiss', 'TISSOperatorConfig')

        clinic = Clinic.objects.create(
            name='Clínica Migração Teste', slug='clinica-migracao-teste',
            cnpj='12.345.678/0001-99', db_name='db_migracao_teste', db_user='u_migracao_teste',
        )

        # Mesmo endpoint + mesmo transporte + mesma credencial (cifrada) nas
        # 3 primeiras — exatamente o cenário do documento: 5 (aqui, 3)
        # operadoras reais atrás da mesma Orizon, credencial idêntica.
        credencial_compartilhada = dict(
            endpoint_url='https://wsp.orizonbrasil.com.br:6213/tiss/v40100/tissSolicitacaoProcedimento',
            gateway_provider='orizon',
            login_encrypted='tok-login-orizon-cifrado',
            senha_encrypted='tok-senha-orizon-cifrado',
        )
        bradesco = OldConfig.objects.create(
            clinic=clinic, nome_operadora='Bradesco', registro_ans='005711', **credencial_compartilhada,
        )
        OldConfig.objects.create(
            clinic=clinic, nome_operadora='Cassi', registro_ans='300700', **credencial_compartilhada,
        )
        OldConfig.objects.create(
            clinic=clinic, nome_operadora='Seguros Unimed', registro_ans='343889', **credencial_compartilhada,
        )
        # Config de transporte DIFERENTE — não pode entrar na mesma connection.
        amil = OldConfig.objects.create(
            clinic=clinic, nome_operadora='Amil', registro_ans='326305',
            endpoint_url='https://webservices.amil.com.br/tiss/', gateway_provider='generico_ans',
            login_encrypted='tok-login-amil-cifrado', senha_encrypted='tok-senha-amil-cifrado',
        )

        # 2. Roda 0010→0012 (schema + backfill 0011 + remoção dos campos antigos).
        self._migrate([('tiss', '0012_tissoperatorconfig_remove_transport_fields')])

        from .models import TISSOperatorConfig, TISSOperatorConnection

        configs_orizon = TISSOperatorConfig.objects.filter(
            registro_ans__in=['005711', '300700', '343889'],
        ).select_related('connection')
        connection_ids = {c.connection_id for c in configs_orizon}
        self.assertEqual(len(connection_ids), 1, 'as 3 configs do mesmo transporte deveriam convergir para 1 connection')

        connection = TISSOperatorConnection.objects.get(pk=connection_ids.pop())
        self.assertEqual(connection.login_encrypted, 'tok-login-orizon-cifrado')
        self.assertEqual(connection.senha_encrypted, 'tok-senha-orizon-cifrado')
        self.assertEqual(connection.endpoint_url, credencial_compartilhada['endpoint_url'])
        self.assertEqual(connection.gateway_provider, 'orizon')

        config_amil = TISSOperatorConfig.objects.select_related('connection').get(registro_ans='326305')
        self.assertNotEqual(config_amil.connection_id, connection.id)
        self.assertEqual(config_amil.connection.login_encrypted, 'tok-login-amil-cifrado')
