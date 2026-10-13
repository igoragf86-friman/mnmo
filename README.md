## Инструкция по установке LLM на серверах с REDOS 8

### Требования к инфраструктуре
- **Количество серверов:** 2 шт.
- **Конфигурация каждого сервера:**
  - 4 ядра CPU;
  - не менее 16 ГБ ОЗУ.
- **Операционная система:** REDOS 8.

---

### Установка зависимостей

Даже при использовании готового бинарника потребуются утилиты для скачивания файлов и управления процессами. Выполните команду:

```bash
sudo dnf install -y wget curl git unzip glibc-all-langpacks
```

---

### Подготовка структуры папок

Создайте директории для размещения компонентов:

```bash
sudo mkdir -p /opt/llama-server/{bin,models}
cd /opt/llama-server
```

---

### Выбор сборки и установка LLM

Для REDOS оптимальна универсальная сборка **CPU+Vulkan**: она работает как на CPU, так и задействует видеокарту через драйверы NVIDIA/AMD без жёсткой привязки к версиям CUDA.

Установите LLM с помощью скрипта:

```bash
curl -LsSf https://llama.app/install.sh | sh
```

#### Проверка установки

Убедитесь, что LLM установлен корректно:

```bash
llama cli --version
```

---

### Загрузка модели Qwen3

Перейдите в папку для моделей:

```bash
cd models
```

Базовая модель (для теории и рассуждений):
Модели можно искать здесь https://huggingface.co

```bash
llama cli --hf-repo Aldaris/Qwen3-8B-Q4_K_M-GGUF --hf-file qwen3-8b-q4_k_m.gguf -p "The meaning to life and the universe is"
```
Модель для эмбендинга текста
```bash
llama server \
 --hf-repo dinab/multilingual-e5-base-Q8_0-GGUF \ 
 --hf-file multilingual-e5-base-q8_0.gguf -c 2048 \
 --port 8081 --host 0.0.0.0 --embeddings
```
---

### Запуск сервера

#### Сервер Chat (порт 8080)

Этот экземпляр предназначен для ответов на теоретические вопросы:

```bash
llama server \
  --hf-repo Aldaris/Qwen3-8B-Q4_K_M-GGUF \
  --hf-file qwen3-8b-q4_k_m.gguf \
  -c 2048 \
  --port 8080 \
  --webui \
  --host 0.0.0.0
```

Доступ к интерфейсу: `http://IP_вашего_сервера:8080`

### Создание базы данных

```bash
docker pull pgvector/pgvector:0.8.7-pg18-trixie

docker run --name postgres_RAG -p 5432:5432 -e POSTGRES_PASSWORD=Password*123 -d pgvector/pgvector:0.8.7-pg18-trixie
```
для включения вектора в обычной базе
```bash
CREATE EXTENSION vector;
```
Для начала  тестово остановимся на  трёх таблиц:

agents — профили, например Huawei и Cisco.
documents — загруженные документы.
chunks — текстовые фрагменты и их эмбеддинги.
В таблице chunks хранятся вместе и текст чанка, и вектор: так поиску легко сопоставить найденный фрагмент с исходным документом.

Схемы. vector(768) так как выбранна модель эмбеддингов выдаёт векторы размерности 768
```bash
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE agents (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    instructions TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_id      BIGINT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    source_name   TEXT,
    version       TEXT,
    content_hash  TEXT,
    status        TEXT NOT NULL DEFAULT 'ready'
                  CHECK (status IN ('processing', 'ready', 'error')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id    BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index    INTEGER NOT NULL,
    content        TEXT NOT NULL,
    embedding      VECTOR(768),
    embedding_model TEXT,
    search_vector  TSVECTOR,
    metadata       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (document_id, chunk_index)
);

CREATE INDEX chunks_document_id_idx
    ON chunks(document_id);

CREATE INDEX chunks_search_vector_idx
    ON chunks USING GIN(search_vector);

CREATE INDEX chunks_embedding_idx
    ON chunks USING HNSW (embedding vector_cosine_ops);


```
```
curl -s http://localhost:8081/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "multilingual-e5-base",
    "input": [
      "query: как оформить возврат товара",
      "passage: Условия возврата товара ..."
    ]
  }' | jq '{
    vector_count: (.data | length),
    dimensions: (.data[0].embedding | length)
  }'
{
  "vector_count": 2,
  "dimensions": 768
```