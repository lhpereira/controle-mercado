# Controle Mercado

Aplicação web para registrar compras a partir de cupons fiscais e acompanhar preços, descontos, estabelecimentos, categorias e hábitos de consumo.

## Executar com Docker

Pré-requisito: Docker Desktop ou Docker Engine com o Compose.

    docker compose up --build

Depois, acesse http://localhost:8000.

Para encerrar:

    docker compose down

O comando abaixo também remove o banco e as imagens enviadas. Use-o somente quando quiser apagar todos os dados:

    docker compose down -v

## Fluxo de uso

1. Abra **Novo cupom**.
2. Fotografe ou selecione uma imagem do cupom.
3. A aplicação coloca o cupom na fila e mostra o andamento enquanto OCR, QR Code e LLM são executados em segundo plano.
4. Quando a leitura terminar, revise estabelecimento, data, totais, itens, descontos e categorias.
5. Confirme a compra para incluí-la no painel.
6. Enriqueça marca, categoria, subcategoria e embalagem na página **Produtos**.

Também é possível começar um cupom manualmente ou informar a URL da NFC-e.

## Dados iniciais

Na primeira execução, a aplicação importa automaticamente o histórico da planilha fornecido em data/seed_compras.json. A importação só ocorre quando o banco está vazio.

## Estrutura dos dados

- receipts: uma linha por cupom, com estabelecimento, data, totais e forma de pagamento.
- receipt_items: itens do cupom, incluindo preço bruto, desconto e valor líquido.
- products: catálogo reutilizável com nome padronizado, marca, categoria e embalagem.

Essa separação permite corrigir a classificação de um produto uma única vez.

Códigos de barras com oito ou mais dígitos são tratados como identificadores globais. PLUs curtos, como `210`, `246` ou `7360`, são identificados pelo par `CNPJ do estabelecimento + Cod`, evitando colisões entre mercados.

## Leitura de imagem

A leitura principal usa RapidOCR e a estrutura fixa da tabela `# | Cod | Descrição | Qt | Un | Vlr | Total`. Primeiro a aplicação localiza o cabeçalho, o rodapé e a quantidade total informada; depois calcula a posição de cada linha e reconhece os itens um a um. Isso evita que uma linha pouco legível faça o OCR pular os itens seguintes.

O número `#` é determinado pela posição da linha, enquanto `Cod` é tratado como o identificador natural do produto. Quando um código já foi revisado no catálogo, seu nome e sua categoria corrigidos são reaproveitados nos próximos cupons. Itens repetidos no mesmo cupom também são comparados entre si para corrigir variações de leitura.

Se uma das linhas não puder ser interpretada, ela ainda é criada na revisão como `Item N — revisar OCR`, preservando a correspondência com a quantidade impressa. A tela informa quantas linhas têm baixa confiança. O Tesseract permanece como alternativa automática para cupons cujo layout não puder ser localizado.

## Revisão com OpenAI ou Ollama

A tela **Novo cupom** oferece quatro modos:

- `hybrid`: RapidOCR primeiro; a LLM recebe a imagem somente quando existem linhas incompletas, baixa confiança ou divergência matemática;
- `rapidocr`: processamento totalmente local, sem chamada de LLM;
- `openai`: interpretação completa pela OpenAI, mantendo o OCR local como fallback;
- `ollama`: interpretação completa por um modelo visual executado localmente.

O modo híbrido é o padrão. A resposta da LLM deve obedecer a um JSON Schema e passa por validações de sequência, código, unidade e `bruto - desconto = total`. Se o provedor falhar ou responder de forma incompleta, o resultado local é preservado e a tela de revisão exibe o motivo.

No modo híbrido, a LLM recebe uma imagem compacta contendo somente as linhas marcadas para revisão, em vez da foto completa do cupom. Isso reduz o tempo de processamento e preserva os itens que o OCR local já reconheceu com confiança.

O processamento de imagens ocorre no serviço `worker`, separado do servidor web. Assim, modelos locais mais lentos não interrompem a resposta do navegador. A tela de andamento pode ser fechada: o processamento continua e o cupom aparece na lista como **Na fila**, **Processando**, **Rascunho** ou **Falhou**. Em caso de falha, é possível tentar novamente sem reenviar a imagem. Reenvios do mesmo formulário reutilizam o registro existente e não criam cupons duplicados.

### OpenAI

Defina no arquivo `.env`:

    LLM_PROVIDER=openai
    OPENAI_API_KEY=sua-chave
    OPENAI_MODEL=gpt-5.6-terra

A chave existe apenas no servidor/container e não é enviada ao navegador. Quando OpenAI é usada, a imagem do cupom é enviada à API.

### Ollama local

Instale o Ollama no computador, escolha um modelo com visão e faça o download, por exemplo:

    ollama pull qwen3-vl:8b

No `.env`:

    LLM_PROVIDER=ollama
    OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
    OLLAMA_MODEL=qwen3-vl:8b

Em Docker Desktop, `host.docker.internal` aponta do container para o computador hospedeiro. Se o Ollama estiver em outro container, use o nome desse serviço na URL.

A integração usa a API nativa `/api/chat` do Ollama, com saída estruturada por JSON Schema e raciocínio desativado. A configuração aceita a URL com ou sem o sufixo `/v1` para manter compatibilidade com instalações existentes.

## QR Code e consulta web

O QR Code é lido com OpenCV. Quando contém uma URL, ela é exibida na revisão.

A consulta automática ao portal fiscal vem desativada:

    NFC_FETCH_ENABLED=false

Para habilitar:

    NFC_FETCH_ENABLED=true
    NFC_ALLOWED_HOSTS=dfe.ms.gov.br,sefaz.ms.gov.br,nfce.sefaz.ms.gov.br

Somente domínios permitidos, endereços públicos e portas HTTP/HTTPS são aceitos. Isso reduz o risco de a aplicação ser usada para acessar serviços internos.

Alguns portais fiscais utilizam CAPTCHA, certificados, JavaScript ou formatos diferentes. Nesses casos, a aplicação preserva a URL e mantém a imagem/OCR como método de entrada.

## Próxima evolução recomendada para NFC-e

Uma segunda etapa pode adotar adaptadores por estado:

1. detectar a UF e o domínio pelo QR Code;
2. consultar o portal autorizado;
3. extrair HTML estruturado ou XML, quando disponível;
4. comparar a resposta fiscal com o OCR;
5. atribuir confiança por campo;
6. solicitar revisão apenas nas divergências.

Esse desenho é mais confiável do que tentar usar um único extrator para todos os portais.

## Configurações

| Variável | Padrão | Finalidade |
|---|---|---|
| SECRET_KEY | valor de desenvolvimento | Proteção da sessão Flask |
| APP_DATA_DIR | /data | Banco e imagens |
| MAX_UPLOAD_MB | 15 | Limite por imagem |
| TESSERACT_LANG | por | Idioma do OCR |
| OCR_PROVIDER | hybrid | `hybrid`, `rapidocr`, `openai` ou `ollama` |
| OCR_CONFIDENCE_THRESHOLD | 0.75 | Limite para revisão pela LLM |
| LLM_PROVIDER | openai | Provedor usado no modo híbrido |
| LLM_TIMEOUT_SECONDS | 300 | Tempo máximo da chamada à LLM no worker |
| OPENAI_API_KEY | vazio | Chave da API OpenAI |
| OPENAI_MODEL | gpt-5.6-terra | Modelo multimodal da OpenAI |
| OLLAMA_BASE_URL | host.docker.internal:11434/v1 | Endpoint compatível com OpenAI |
| OLLAMA_MODEL | qwen3-vl:8b | Modelo visual instalado no Ollama |
| WORKER_POLL_SECONDS | 1 | Intervalo entre consultas do worker à fila |
| PROCESSING_STALE_MINUTES | 20 | Tempo para retomar uma tarefa interrompida |
| NFC_FETCH_ENABLED | false | Consulta automática da URL |
| NFC_ALLOWED_HOSTS | portais de MS | Domínios fiscais autorizados |

## Testes

Dentro do container:

    python -m unittest discover -s tests -v

## Observações

- O banco padrão é SQLite, adequado para uso pessoal ou poucos usuários.
- Para vários usuários simultâneos, a camada de persistência pode ser migrada para PostgreSQL.
- Os gráficos usam Chart.js por CDN. O restante da aplicação continua acessível sem os gráficos.
