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
3. A aplicação executa OCR e tenta ler o QR Code.
4. Revise estabelecimento, data, totais, itens, descontos e categorias.
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

## Leitura de imagem

O container inclui Tesseract OCR com o idioma português. Antes do reconhecimento, a imagem é orientada, convertida para tons de cinza, redimensionada, reforçada em contraste e filtrada para reduzir ruído.

O parser reconhece o padrão mais comum de NFC-e brasileira: código, descrição, quantidade, unidade, preço unitário, total e linhas de desconto. Como cupons variam entre redes e estados, toda extração passa por revisão humana.

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
| NFC_FETCH_ENABLED | false | Consulta automática da URL |
| NFC_ALLOWED_HOSTS | portais de MS | Domínios fiscais autorizados |

## Testes

Dentro do container:

    python -m unittest discover -s tests -v

## Observações

- O banco padrão é SQLite, adequado para uso pessoal ou poucos usuários.
- Para vários usuários simultâneos, a camada de persistência pode ser migrada para PostgreSQL.
- Os gráficos usam Chart.js por CDN. O restante da aplicação continua acessível sem os gráficos.
