"""
clinic_billing — faturamento CLÍNICO (o que a clínica cobra de paciente/convênio),
não o faturamento SaaS da Syncro (assinatura da clínica — esse é o app `billing`).

TASK-BO-R07: este app existe para resolver a colisão de nome antes de qualquer
model de faturamento clínico ser criado. `billing/` é e continua sendo
exclusivamente Plan/Invoice da assinatura da clínica com a Syncro — nunca deve
ganhar um model que represente dinheiro que a CLÍNICA recebeu de paciente ou
operadora de convênio.

TASK-BO-R01 (sync strategy de faturamento/procedimentos) cria `BillingEntry`
aqui — não em `billing/` nem em `clinics/`.
"""
