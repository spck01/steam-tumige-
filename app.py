import streamlit as st
import streamlit.components.v1 as components
import urllib.parse
import re
import json
import base64
import time
import requests
from PIL import Image
from io import BytesIO
import io

# ==========================================
# 1. アプリの設定
# ==========================================
APP_URL = "https://steam-tsumige.streamlit.app/"
API_KEY = st.secrets["STEAM_API_KEY"] # secrets.tomlからAPIキーを読み込む
TSUMI_THRESHOLD_MINUTES = 600 # 「積みゲー」と判定するプレイ時間の上限（10時間）
COOLDOWN_SECONDS = 5 # 「可視化する」ボタンの連打防止インターバル

st.set_page_config(page_title="積みゲー晒しジェネレーター", page_icon="📦")

# ==========================================
# 2. 内部関数の定義
# ==========================================
def get_steam_login_url():
    steam_openid_url = "https://steamcommunity.com/openid/login"
    params = {
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.mode": "checkid_setup",
        "openid.return_to": APP_URL,
        "openid.realm": APP_URL,
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
    }
    return f"{steam_openid_url}?{urllib.parse.urlencode(params)}"

def verify_steam_login():
    """SteamからのリダイレクトをSteam側に検証させ、正当な場合のみSteamIDを返す（なりすまし防止）"""
    if "openid.claimed_id" not in st.query_params:
        return None

    # Steamが返してきたパラメータをそのまま検証依頼として投げ返す
    verify_params = dict(st.query_params)
    verify_params["openid.mode"] = "check_authentication"

    try:
        verify_res = requests.post(
            "https://steamcommunity.com/openid/login",
            data=verify_params,
            timeout=5,
        )
    except requests.RequestException:
        return None

    if "is_valid:true" not in verify_res.text:
        return None

    claimed_id = verify_params.get("openid.claimed_id", "")
    match = re.search(r"https://steamcommunity\.com/openid/id/(\d+)", claimed_id)
    return match.group(1) if match else None

def fetch_owned_games(steam_id):
    """Steam APIから所持ゲームリストを取得する"""
    url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v0001/"
    params = {
        "key": API_KEY,
        "steamid": steam_id,
        "include_appinfo": 1, # ゲーム名とアイコンURLを含める（必須）
        "format": "json"
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None

def generate_library_image(games_list, max_games=64):
    """ゲームのアイコンをダウンロードして1枚の画像に合成する"""
    # 【変更点】プレイ時間が短い順（0分優先）に並び替えて、上位を取得
    sorted_games = sorted(games_list, key=lambda x: x.get('playtime_forever', 0))[:max_games]
    
    icon_size = 64 # 1つのアイコンのサイズ(ピクセル)
    cols = 8 # 横に並べる数（8×8=64個）
    rows = (len(sorted_games) + cols - 1) // cols
    
    if rows == 0:
        return None
        
    # キャンバス（背景）の作成（Steamっぽい濃いブルーグレー）
    canvas_width = cols * icon_size
    canvas_height = rows * icon_size
    canvas = Image.new('RGB', (canvas_width, canvas_height), (23, 26, 33))
    
    # プログレスバー用
    progress_text = "積みゲーのホコリを払って並べています..."
    my_bar = st.progress(0, text=progress_text)
    
    for i, game in enumerate(sorted_games):
        appid = game['appid']
        icon_hash = game.get('img_icon_url')
        
        if icon_hash:
            # アイコン画像のURLを組み立ててダウンロード
            icon_url = f"https://media.steampowered.com/steamcommunity/public/images/apps/{appid}/{icon_hash}.jpg"
            try:
                img_res = requests.get(icon_url, timeout=5)
                if img_res.status_code == 200:
                    img = Image.open(BytesIO(img_res.content)).convert("RGB")
                    img = img.resize((icon_size, icon_size))
                    
                    # キャンバスに貼り付け
                    x = (i % cols) * icon_size
                    y = (i // cols) * icon_size
                    canvas.paste(img, (x, y))
            except Exception as e:
                print(f"Error loading {appid}: {e}")
                
        # プログレスバーの更新
        my_bar.progress((i + 1) / len(sorted_games), text=progress_text)
        
    my_bar.empty() # 終わったらプログレスバーを消す
    return canvas

# ==========================================
# 3. 画面のUI構築
# ==========================================
# 【変更点】タイトルとコンセプトの変更
st.title("📦 積みゲー晒しジェネレーター")

if "steam_id" not in st.session_state:
    st.session_state.steam_id = None

if "openid.claimed_id" in st.query_params:
    extracted_id = verify_steam_login()
    st.query_params.clear()
    if extracted_id:
        st.session_state.steam_id = extracted_id
    else:
        st.session_state.login_error = True
    st.rerun()

if st.session_state.pop("login_error", False):
    st.error("❌ Steamログインの検証に失敗しました。お手数ですが、もう一度ログインし直してください。")

# ログイン後の画面
if st.session_state.steam_id:
    st.success(f"✅ ログイン中 (SteamID: {st.session_state.steam_id})")
    
    if st.button("自分の積みゲーを可視化する！", type="primary"):
        now = time.time()
        if now - st.session_state.get("last_run_time", 0) < COOLDOWN_SECONDS:
            st.warning(f"⏳ 少し間隔を空けてから再度お試しください（{COOLDOWN_SECONDS}秒に1回まで）。")
            st.stop()
        st.session_state.last_run_time = now

        with st.spinner("Steamの奥底から積まれたゲームを探しています..."):
            # 1. APIからデータ取得
            api_data = fetch_owned_games(st.session_state.steam_id)
            
            # データの中身をチェック
            if api_data is None:
                st.error("❌ Steamサーバーとの通信に失敗しました。しばらくしてから再度お試しください。")
            elif "response" in api_data and "games" in api_data["response"]:
                games = api_data["response"]["games"]
                game_count = api_data["response"]["game_count"]
                
                # 【変更点】プレイ時間10時間（600分）以下のゲームだけを抽出
                tsumi_games = [g for g in games if g.get('playtime_forever', 0) <= TSUMI_THRESHOLD_MINUTES]
                tsumi_count = len(tsumi_games)
                tsumi_ratio = (tsumi_count / game_count * 100) if game_count else 0

                m1, m2, m3 = st.columns(3)
                m1.metric("🎮 所持ゲーム総数", f"{game_count}本")
                m2.metric("📦 積みゲー数", f"{tsumi_count}本")
                m3.metric("💦 積みゲー率", f"{tsumi_ratio:.1f}%")
                st.caption(f"📏 判定基準：累計プレイ時間が **{TSUMI_THRESHOLD_MINUTES // 60}時間（{TSUMI_THRESHOLD_MINUTES}分）以下** のゲームを「積みゲー」と定義しています。")

                # 2. 画像の合成（積みゲーリストを渡す）
                result_image = generate_library_image(tsumi_games, max_games=64)
                
                if result_image:
                    st.image(result_image, caption=f"手付かずのゲームたち (上位 {min(tsumi_count, 64)}本)")
                    st.divider()

                    # ==========================================
                    # X (Twitter) シェア機能
                    # ==========================================
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        # 1. 画像をダウンロード可能な形式（バイナリ）に変換
                        buf = io.BytesIO()
                        result_image.save(buf, format="PNG")
                        byte_im = buf.getvalue()
                        
                        # 2. ダウンロードボタン
                        st.download_button(
                            label="📥 戒めとして画像を保存する",
                            data=byte_im,
                            file_name="steam_tsumige.png",
                            mime="image/png",
                            use_container_width=True
                        )
                        
                    with col2:
                        # 3. 定型文とXの投稿URL（Web Share API非対応時のフォールバック用）
                        tweet_text = f"私のSteam所持ゲーム{game_count}本のうち、積みゲー（10時間以下）は【{tsumi_count}本】でした😇\nいつかやります…！\n#積みゲー晒し #Steam"
                        tweet_url = f"https://twitter.com/intent/tweet?text={urllib.parse.quote(tweet_text)}"

                        # 4. 画像をBase64化してJSに渡す（Web Share APIで画像を直接共有する）
                        b64_img = base64.b64encode(byte_im).decode()
                        share_html = f"""
                        <style>
                            html, body {{
                                margin: 0;
                                padding: 0;
                                background: transparent;
                            }}
                            #shareBtn {{
                                background-color: #000000;
                                color: #ffffff;
                                border: 1px solid rgba(250, 250, 250, 0.2);
                                box-sizing: border-box;
                                padding: 0.375rem 0.75rem;
                                font-size: 16px;
                                border-radius: 0.5rem;
                                cursor: pointer;
                                font-weight: 400;
                                width: 100%;
                                height: 2.5rem;
                                font-family: "Source Sans Pro", sans-serif;
                                line-height: 1.6;
                                transition: border-color 0.2s, color 0.2s;
                            }}
                            #shareBtn:hover {{
                                border-color: #ffffff;
                                color: #ffffff;
                            }}
                            #shareBtn:active {{
                                background-color: #222222;
                            }}
                        </style>
                        <button id="shareBtn">𝕏 で罪を告白する</button>
                        <script>
                        const b64Data = "{b64_img}";
                        const tweetText = {json.dumps(tweet_text)};
                        const fallbackUrl = {json.dumps(tweet_url)};

                        document.getElementById('shareBtn').addEventListener('click', async () => {{
                            try {{
                                const byteChars = atob(b64Data);
                                const byteNumbers = new Array(byteChars.length);
                                for (let i = 0; i < byteChars.length; i++) {{
                                    byteNumbers[i] = byteChars.charCodeAt(i);
                                }}
                                const byteArray = new Uint8Array(byteNumbers);
                                const file = new File([byteArray], "steam_tsumige.png", {{ type: "image/png" }});

                                if (navigator.canShare && navigator.canShare({{ files: [file] }})) {{
                                    await navigator.share({{ files: [file], text: tweetText }});
                                }} else {{
                                    window.open(fallbackUrl, "_blank");
                                    alert("この端末では画像の自動共有に対応していないため、Xの投稿画面のみ開きます。画像は手動で添付してください。");
                                }}
                            }} catch (err) {{
                                if (err.name !== 'AbortError') {{
                                    console.error(err);
                                }}
                            }}
                        }});
                        </script>
                        """
                        components.html(share_html, height=45)
                else:
                    st.success("素晴らしい！積みゲーは1本もありませんでした！🎉")
            else:
                # 🚨 最もよくあるエラー（プライバシー設定）への対応
                st.error("❌ ゲーム情報の取得に失敗しました。")
                st.warning("""
                **【原因として考えられること】**
                あなたのSteamプロフィールの「ゲームの詳細」が非公開になっている可能性があります。
                Steamの「プロフィールを編集」＞「プライバシー設定」から、**「ゲームの詳細」を公開**にしてから再度お試しください。
                """)
                
    st.divider()
    if st.button("ログアウト"):
        st.session_state.steam_id = None
        st.rerun()
else:
    st.write("Steamアカウントと連携して、積み上げられたままのゲームを可視化します。")
    login_url = get_steam_login_url()
    st.markdown(
        f'<a href="{login_url}" target="_self" rel="noreferrer">'
        f'<button style="background-color: #171a21; color: white; border: none; padding: 10px 20px; font-size: 16px; border-radius: 5px; cursor: pointer; font-weight: bold;">'
        f'Steamでログイン</button></a>',
        unsafe_allow_html=True
    )

    with st.expander("🔒 プライバシーポリシー"):
        st.markdown("""
**このサービスは、Valve Corporationとは無関係の非公式ツールです。**

#### ログインについて
- **パスワードは一切入力しません。** ログインボタンはSteam公式サイト（steamcommunity.com）に移動するだけで、認証はSteamの画面上で直接行われます（OpenID方式）。IDやパスワードが本アプリを経由・通過することはありません。
- 本アプリがSteamから受け取るのは、**SteamID（公開ID）のみ**です。パスワードやメールアドレス、支払い情報等は一切取得できません。

#### 取得・利用する情報
- ログイン後、Steam公開Web API経由で**所持ゲームのリスト（ゲーム名・アイコン画像・プレイ時間）** を取得します。
- これらは画像生成（積みゲー画像の合成）という**その場限りの処理**のみに利用します。

#### データの保存について
- 取得したSteamIDやゲーム情報を**データベースやファイルとしてサーバーに保存することはありません**。
- 情報はブラウザとの接続が続く間のみメモリ上に保持され、**ページを閉じる／ログアウトすると破棄**されます。
- 生成された画像も同様にサーバー上には保存されません（お使いの端末上でのみダウンロード・共有されます）。

#### X（旧Twitter）への共有機能について
- 「𝕏で罪を告白する」ボタンは、お使いの端末のOS標準の共有機能（Web Share API）を呼び出すだけです。
- 画像はお使いの端末のブラウザ内で処理され、本アプリのサーバーを経由せずに共有シートへ渡されます。
- 実際に投稿するかどうかは、共有画面でユーザー自身が選択・操作するまで確定しません（自動投稿は行いません）。

#### APIキーについて
- Steam Web APIキーはサーバー側にのみ保管されており、ブラウザ（利用者側）に渡ることはありません。

#### 通信の暗号化
- 本アプリおよびSteamとの通信はすべてHTTPSで暗号化されています。

#### Cookie・トラッキングについて
- 広告目的のトラッキングCookieや、第三者への行動データ提供は行っていません。
        """)