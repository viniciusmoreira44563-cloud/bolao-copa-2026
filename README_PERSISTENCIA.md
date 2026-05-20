# Correção de persistência

Esta versão corrige dois pontos:

1. Usuários iniciais só são criados se a tabela de usuários estiver vazia.
   Assim, quando o admin exclui alguém, esse usuário não volta automaticamente.

2. Fotos dos usuários passam a ser salvas também no banco SQLite (`avatar_data`).
   Isso evita depender apenas da pasta `static/uploads`.

Observação importante:
Se o Render recriar o banco inteiro a cada deploy, ainda será necessário usar banco persistente
(PostgreSQL ou Persistent Disk) para manter usuários/fotos entre deploys.
