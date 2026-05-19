
# App Mobile - Exemplo de integração

Este é um exemplo simples para o futuro app mobile.

A API principal já está pronta no backend Flask.

## Endpoints

### Login

POST `/api/login`

```json
{
  "name": "Vinicius",
  "password": "123456"
}
```

Retorna:

```json
{
  "token": "...",
  "user": {
    "id": 1,
    "name": "Vinicius",
    "avatar": null
  }
}
```

### Listar jogos

GET `/api/matches`

### Salvar palpite

POST `/api/guesses`

Header:

```text
Authorization: Bearer SEU_TOKEN
```

Body:

```json
{
  "match_id": 1,
  "guess_home": 2,
  "guess_away": 1
}
```

### Ranking

GET `/api/ranking`

### Upload de avatar

POST `/api/avatar`

Header:

```text
Authorization: Bearer SEU_TOKEN
```

Form-data:

```text
avatar = arquivo de imagem
```
