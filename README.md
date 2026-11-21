# Caixa AL-1182 no Linux

Aplicativo GTK 3 para a caixa Bluetooth Altomex AL-1182. A janela mostra o estado da conexão e oferece **Reparar agora** e **Testar som**. O reparo automático roda na sessão do usuário, mesmo com a janela fechada.

## Instalar

1. Baixe [caixa-al1182-instalar.sh](https://github.com/rafaelmendoncadev/caixa-al1182-linux/releases/latest/download/caixa-al1182-instalar.sh).
2. Abra um terminal na pasta do download e execute:

   ```sh
   sh caixa-al1182-instalar.sh
   ```

3. Abra **Caixa AL-1182** no menu de aplicativos ou pelo atalho da área de trabalho. Em alguns ambientes, o gerenciador de arquivos pede para marcar o atalho como confiável.

O instalador é autônomo e pode ser executado novamente para atualizar ou corrigir a instalação. Use-o na sua sessão normal, sem sudo. O [SHA-256](https://github.com/rafaelmendoncadev/caixa-al1182-linux/releases/latest/download/caixa-al1182-instalar.sh.sha256) pode ser verificado com `sha256sum -c caixa-al1182-instalar.sh.sha256` na pasta dos dois downloads.

### Requisitos

Linux com BlueZ, PipeWire (incluindo a compatibilidade com PulseAudio), WirePlumber, systemd de usuário, Python 3 com PyGObject e GTK 3. A sessão precisa oferecer `pactl`, `paplay`, `pw-dump` e `pw-cli`. Em sistemas da família Debian, os pacotes dessas ferramentas ficam normalmente em `pulseaudio-utils` e `pipewire-bin`, e o GTK usa `python3-gi` e `gir1.2-gtk-3.0`. O empacotamento foi validado no Linux Mint com Cinnamon, BlueZ 5.72, PipeWire 1.0.5 e WirePlumber 0.4.17.

## Como usar

- **Reparar agora** tenta recuperar o áudio Bluetooth A2DP e escolhe a caixa como saída padrão. Com a caixa desligada, distante ou ocupada por outro aparelho, a tentativa termina em tempo limitado e a janela continua utilizável.
- **Testar som** envia três notas curtas diretamente para a caixa, respeitando o volume e o mudo do sistema.
- O timer começa cerca de 20 segundos depois que o systemd de usuário inicia e volta a rodar cerca de 45 segundos após o fim de cada checagem. Ele mantém a saída escolhida pelo usuário quando a caixa já tem áudio A2DP. Quando o motor recupera a caixa, seleciona-a como padrão.
- Se o pareamento não estiver disponível, o aplicativo busca a AL-1182. Na primeira associação, usa o nome exato e exige um candidato inequívoco compatível com áudio. Uma vez reconhecida, a caixa fica associada localmente pelo endereço Bluetooth. Ligue-a em modo de pareamento se a busca não a encontrar. Para trocar de caixa, pareie a nova pelo menu Bluetooth e remova o arquivo `dispositivo` de `${XDG_STATE_HOME:-$HOME/.local/state}/caixa-al1182-linux/`.
- Quando o aplicativo desktop ChatGPT abre um fluxo para a AL-1182, a checagem pode restaurar o ganho escalar interno do PipeWire se ele estiver zerado apesar de o fluxo e a caixa estarem audíveis para o mixer. A identidade do fluxo é conferida no PulseAudio e no PipeWire. Chrome e outros programas, bem como os níveis de volume e mudo escolhidos no mixer, ficam fora desse reparo.

O aplicativo usa os nomes de unidade `al1182-reconnect.service` e `al1182-reconnect.timer`. Quando já existem, o instalador usa o mesmo timer e redireciona o serviço com um **drop-in**. Para verificar a automação:

```sh
systemctl --user status al1182-reconnect.timer
journalctl --user -u al1182-reconnect.service -n 20
```

## Desinstalar

Na pasta do instalador, execute:

```sh
sh caixa-al1182-instalar.sh --desinstalar
```

Em uma instalação nova, isso desativa e remove as unidades do aplicativo. Quando havia um reconector anterior, isso remove o complemento do aplicativo e restaura o estado anterior do timer. O menu, o atalho, o ícone e a associação local do aplicativo também são removidos. O pareamento Bluetooth fica no sistema.

## Gerar o instalador a partir do código

```sh
python3 tools/empacotar.py
```

O comando gera `caixa-al1182-instalar.sh` e o arquivo SHA-256 na raiz do repositório. O pacote incorpora apenas os arquivos do aplicativo, o ícone e os modelos de integração.
