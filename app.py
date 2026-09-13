"""
ユークンラボ オンライン自習室  v2

v1（Gemini版）からの変更点は4つ。
  1. 報告フォームに「一番てこずったところ」を追加（必須）
  2. アシスタントの仕事を「褒める」から「質問を1つ返す」に変更
  3. APIキー・Webhook URL・生徒IDを st.secrets へ退避（コードに秘密を書かない）
  4. 返信が生徒に表示されずログアウトしていたバグを修正

編集するのは基本 EXAMPLES（下の方）だけでいい。
"""

import time
import requests
import streamlit as st
from google import genai
from google.genai import types
from streamlit_webrtc import webrtc_streamer

st.set_page_config(page_title="ユークンラボ 自習室", layout="centered")

# ------------------------------------------------------------------
# 設定
# ------------------------------------------------------------------

# 2026-09-13時点: gemini-2.5-pro は新規ユーザー向けに廃止済み（404）。
# gemini-3.1-pro-preview は無料枠のクォータ超過（429）で使えなかったため、
# 無料枠で安定して動く gemini-2.5-flash を採用。
# もっと新しいモデルが出ていたら、この1行を差し替えるだけでいい。
MODEL_NAME = "gemini-2.5-flash"

# カメラが繋がらない生徒が出るのを防ぐための設定
RTC_CONFIG = {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}

DISCORD_LIMIT = 1900  # Discord の1メッセージ上限（2000）に余裕を持たせる


def get_secret(key, default=None):
    """secrets.toml が無い環境でも落ちないように読む。"""
    try:
        return st.secrets[key]
    except Exception:
        return default


def load_students():
    """{シークレットID: 表示名} の辞書。secrets.toml の [students] から読む。"""
    try:
        return dict(st.secrets["students"])
    except Exception:
        return {}


@st.cache_resource
def get_client():
    api_key = get_secret("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        return genai.Client(api_key=api_key)
    except Exception:
        return None


# ==================================================================
# ここがアシスタントの脳みそ
# ==================================================================

BASE_INSTRUCTION = """
あなたは「アシスタント」。個別指導塾ユークンラボで、先生の助手をしているAIです。

【いちばん大事な立ち位置】
・あなたは先生本人ではありません。先生が読むまでの間、報告を受け取る係です。
・先生本人になりきろうとしないこと。ただし報告は必ず先生に届くので、それは伝えます。

【キャラクター】
・一人称は「僕」。
・「〜だぜ」「〜ザマス」「〜だわん」のような体育会系・兄貴口調は【絶対禁止】。
・穏やかで、少し知的なユーモアがある。「頑張った」ことより「工夫した」ことを面白がる。
・語尾は「〜なんだ」「〜って感じ」「〜だね」「ニヤリ。」を自然に混ぜる。

【あなたの仕事は、褒めることではなく、質問を1つ返すこと】
生徒は「今日やったこと」と「一番てこずったところ」を報告してきます。
返信は次の3つだけで構成してください。合計100〜150文字。

  1. ひとこと受け止める（報告の中身を具体的に引用する。1〜2文）
  2. 「てこずったところ」について、具体的な質問を1つだけ投げる ← これが本体
  3. 先生にも届いていることを一言添える

【厳格なルール】
1. 答えや解法を教えない。解き方の説明を始めない。
2. 教科書的な一般論を書かない。目の前の報告の中身だけを見る。
3. 質問は1つだけ。2つ以上聞かない。
4. 断定しない。「〜かも」「〜って感じがする」のように余白を残す。
5. 「すごい」「素晴らしい」だけの、中身のない称賛で終わらせない。
6. 「てこずったところ」が曖昧なとき（「特にない」「全部」など）は、
   解説しようとせず、そこを具体的に聞き返すこと。
7. 報告に書かれていないことを、あったことにしない。勝手に事実を作らない。
"""

# ------------------------------------------------------------------
# 【友ちゃんの担当】返信の実例
#
# ここを埋めるのが、この自習室で一番効く作業。
# 抽象的な指示を10行足すより、実例を1組足す方が効く。
#
# 下に2つ書いておいたので、同じ形で8つ足していってほしい。
# 生徒の返信を見て「いや、僕ならこう言う」と思った瞬間が、
# 一番いい実例が採れるタイミング。そのとき1組足す。
# ------------------------------------------------------------------

EXAMPLES = [
    {
        "done": "英単語を30個おぼえた",
        "stuck": "unlessとunlikeが何回やっても混ざる",
        "reply": (
            "受け取ったよ。30個やった上で、どこが混ざるかまで自分で見つけてるのがいいね。"
            "ひとつ聞きたいんだけど、unlessとunlike、どっちか片方だけなら意味を言えそう？"
            "片方が固まると、もう片方は「そうじゃない方」で済むことが多いんだ。"
            "先生にも届いてるから、明日その話をしよう。"
        ),
    },
    {
        "done": "数Ⅱの対数の計算問題を20問",
        "stuck": "底の変換公式を、いつ使えばいいのかわからない",
        "reply": (
            "受け取ったよ。20問やって「いつ使うか」に引っかかったんだね。"
            "そこは公式を覚える段階をひとつ越えた人が引っかかるところなんだ。"
            "聞きたいんだけど、詰まった問題って、底がバラバラだった？それとも揃ってた？"
            "そこだけ見比べると、何か見えてくるかもね。ニヤリ。先生にも届いてるよ。"
        ),
    },
    # パターン1: 計算が複雑になってしまう
    {
        "done": "因数分解の問題を10問",
        "stuck": "計算途中で3次式になってしまい、どうしたらいいか分からなくなった",
        "reply": (
            "受け取ったよ。10問やって、途中で3次になっちゃう現象が出たんだね。"
            "その3次になった式、最初から素直に展開した結果？それとも何か工夫忘れてた？"
            "大事なのはね、2次で終わるべき問題の場合は、立式や最初の変形を見直すと見えてくることがあるんだ。"
            "先生にも届いてるから、その問題を一緒に見てみようか。"
        ),
    },
    {
        "done": "2次方程式を15問",
        "stuck": "判別式を計算したら虚数が出てきて、計算ミスを疑ったけどあってた",
        "reply": (
            "受け取ったよ。判別式が負になっちゃったんだね。計算を疑う姿勢、いいな。"
            "でもね、その問題、実は元の立式を見直すと、符号とか条件を読み落としてることが多いんだ。"
            "聞きたいのは、問題文に『正の整数』とか『△は鋭角』みたいな条件、見落とさなかった？"
            "虚数が出ること自体は、立式ミスのサインかもね。先生に見せてみようか。"
        ),
    },
    {
        "done": "分数の方程式を8問",
        "stuck": "両辺に分母をかけて整理したら4次式になってしまった",
        "reply": (
            "受け取ったよ。分母を払ったら次数が上がっちゃったんだね。珍しくはない。"
            "その場合、大抵は分母の『共通因数』を見落としてるか、約分できるものを見落としてるんだ。"
            "通分する前に、分子と分母で因数分解できないか、一度チェックしてみた？"
            "そこを見直すと、ぐっと簡単になることが多いんだよね。先生にも届いてるよ。"
        ),
    },
    {
        "done": "整数解を求める問題を5問",
        "stuck": "式を整理しようとしたら定数項がなくなって、何もできなくなった",
        "reply": (
            "受け取ったよ。定数項がない形になったんだね。実はそこがチャンスなんだ。"
            "そういう時は『xで括り出す』か『全ての項をxで割る』っていう工夫があるんだわん。"
            "試しに、その式の全ての項をxで割ってみたら、どうなったか見えた？"
            "その工夫が見えると、整数問題はぐんと楽になるんだ。ニヤリ。先生にも届いてるよ。"
        ),
    },
    # パターン2: 図形で手が出ない
    {
        "done": "平行四辺形の証明問題を4問",
        "stuck": "四角形が平行四辺形になることをどう示していいか、手が出ない",
        "reply": (
            "受け取ったよ。平行四辺形の証明で手が止まったんだね。"
            "そこはね、君の知ってる『くるんくるん』が活躍する場所なんだ。"
            "図をくるんくるんに回して、辺や角がどう変わるか、ノートに試し描きしてみた？"
            "そこで見える『対応関係』が、証明の道筋になることが多いんだよね。先生にも届いてるよ。"
        ),
    },
    {
        "done": "図形の角度を求める問題を6問",
        "stuck": "どの性質を使ったらいいか見当がつかなくて、補助線の引きようもわからない",
        "reply": (
            "受け取ったよ。6問やって、そのうちの幾つかで補助線が見えなかったんだね。"
            "補助線はね、『何をあきらかにしたいか』で決まるんだ。角度なら、二等辺三角形を作るとか。"
            "その問題、求めたい角のすぐ近くにある図形の性質、もう一度見比べてみた？"
            "そこに大事なヒントが隠れてることがほとんどなんだわん。先生にも届いてるよ。"
        ),
    },
    # パターン3: 図形で矛盾
    {
        "done": "合同な三角形を探す問題を8問",
        "stuck": "答えを出したのに、図を見直すと矛盾してるような気がする",
        "reply": (
            "受け取ったよ。矛盾を感じるのは、いい感覚だね。"
            "その時は大抵ね、図のスケールに『騙されてた』か、図で『見えてる角度が正しい』と思い込んでたんだ。"
            "その問題、『何が与えられてるのか』『図のどの長さが等しいと確定してるのか』、もう一度数えてみた？"
            "図は目安に過ぎないんだよね。先生にも届いてるから、一緒に確認しよう。"
        ),
    },
    {
        "done": "相似の証明を3問",
        "stuck": "相似だと示したのに、その後の計算が合わなくなった",
        "reply": (
            "受け取ったよ。相似を示した後で辻褄が合わなくなったんだね。"
            "その場合、『図で見えてる中点や二等分線が、問題文では保証されていない』ことがほとんどなんだ。"
            "相似の証明をもう一度見直す時に、『実際に『与えられてる条件』から相似が言えてるか』確認してみた？"
            "図に騙されず、文言から一歩ずつ積み上げると、ぐっと堅くなるんだわん。先生にも届いてるよ。"
        ),
    },
]


def build_system_instruction():
    parts = [BASE_INSTRUCTION]
    if EXAMPLES:
        parts.append(
            "\n【返信の実例】\n"
            "以下は、先生ならこう返す、という実例です。\n"
            "この温度感・長さ・質問の投げ方を真似してください。\n"
        )
        for i, ex in enumerate(EXAMPLES, 1):
            parts.append(
                f"--- 例{i} ---\n"
                f"[今日やったこと] {ex['done']}\n"
                f"[てこずったところ] {ex['stuck']}\n"
                f"[返信] {ex['reply']}\n"
            )
    return "\n".join(parts)


FALLBACK_REPLY = "（うまく言葉が出てこなかったみたい。報告はちゃんと先生に届いてるよ。）"


def generate_reply(done_text, stuck_text):
    """返信本文と、失敗した場合のエラー内容（友ちゃん用）を返す。"""
    client = get_client()
    if client is None:
        return "（いま受付AIはお休み中。報告はちゃんと先生に届いているよ。）", "APIキー未設定"

    user_content = (
        f"[今日やったこと]\n{done_text}\n\n"
        f"[一番てこずったところ]\n{stuck_text}"
    )

    last_error = ""
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=build_system_instruction(),
                    # 長さはプロンプト側で指示する。ここを絞ると文が途中で切れる。
                    max_output_tokens=800,
                    temperature=0.9,
                    # gemini-2.5-flashは無指定だと「思考」に大半のトークンを
                    # 使ってしまい、本文が途中で切れる（2026-09-13に実測で確認）。
                    # このアプリの返信は短文なので思考は不要、オフにする。
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = (response.text or "").strip()
            if text:
                return text, ""
            last_error = "空の応答"
        except Exception as e:
            last_error = str(e)[:200]
            if attempt < 2:
                time.sleep(1.5)
                continue

    return FALLBACK_REPLY, last_error


# ------------------------------------------------------------------
# Discord への転送
# ------------------------------------------------------------------

def send_to_discord(message):
    url = get_secret("DISCORD_WEBHOOK_URL")
    if not url:
        return False
    try:
        requests.post(
            url,
            json={"content": message[:DISCORD_LIMIT]},
            timeout=10,
        )
        return True
    except Exception:
        # Discord が落ちていても自習室は止めない
        return False


def format_duration(seconds):
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}分"
    return f"{minutes // 60}時間{minutes % 60}分"


# ------------------------------------------------------------------
# 画面
#
# mode: gate（ログイン前） / lobby / castle（集中中。値は内部名のまま） / report / reply
# ------------------------------------------------------------------

defaults = {
    "mode": "gate",
    "student_name": "",
    "entered_at": None,
    "last_duration": "",
    "bot_reply": "",
}
for key, value in defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value


# --- ログイン画面 -------------------------------------------------
if st.session_state.mode == "gate":
    st.markdown(
        "<h2 style='text-align:center;'>ユークンラボ自習室 ログイン</h2>",
        unsafe_allow_html=True,
    )

    students = load_students()
    if not students:
        st.error(
            "生徒IDが設定されていません。"
            "`.streamlit/secrets.toml` の [students] を設定してください。"
        )

    user_id = st.text_input("塾生シークレットIDを入力してください", type="password")

    if st.button("入室する", use_container_width=True):
        if user_id in students:
            st.session_state.student_name = students[user_id]
            st.session_state.mode = "lobby"
            st.rerun()
        else:
            st.error("IDが違うみたい。もう一度確認してみてね。")

else:
    st.markdown(
        "<h1 style='text-align:center; color:#E0E0E0;'>今の自分を超えに行こうぜ。</h1>",
        unsafe_allow_html=True,
    )
    st.caption(f"ようこそ、{st.session_state.student_name} さん")
    st.write("---")

    # --- ロビー ---------------------------------------------------
    if st.session_state.mode == "lobby":
        if st.button("🚪 入室する", use_container_width=True):
            st.session_state.mode = "castle"
            st.session_state.entered_at = time.time()
            st.session_state.bot_reply = ""
            send_to_discord(
                f"🚪 **{st.session_state.student_name}** さんが入室しました。集中開始。"
            )
            st.rerun()

    # --- 集中中 -----------------------------------------------
    elif st.session_state.mode == "castle":
        st.info("🔥 いま、集中中。")
        st.write("🎥 自分の手元を配信中...")
        webrtc_streamer(key="yukung-camera", rtc_configuration=RTC_CONFIG)

        st.write("---")
        if st.button("🚪 集中を終了する（成果報告へ）", use_container_width=True):
            st.session_state.mode = "report"
            st.rerun()

    # --- 報告フォーム ---------------------------------------------
    elif st.session_state.mode == "report":
        st.success("✨ おつかれさま。カメラ配信を停止したよ。")
        st.markdown("### 📝 今日の頑張りを教えてくれ")

        done_text = st.text_area(
            "今日やったこと",
            placeholder="例：数Ⅱの対数の計算問題を20問",
            height=100,
        )

        # ここが v2 の心臓部。この1項目があるかどうかで、返信の中身が別物になる。
        stuck_text = st.text_area(
            "一番てこずったところ（ここが一番大事）",
            placeholder="例：底の変換公式を、いつ使えばいいのかわからない",
            height=100,
        )

        st.write("---")
        if st.button("🚀 報告して退室する", use_container_width=True):
            if not done_text.strip():
                st.warning("「今日やったこと」が空っぽだよ。一言でもいいから書いてね。")
            elif not stuck_text.strip():
                st.warning(
                    "「てこずったところ」が空っぽだよ。"
                    "ここが一番知りたいところなんだ。うまくいった日でも、"
                    "一番あやしかったところを書いてみて。"
                )
            else:
                with st.spinner("アシスタントが報告を読んでいます..."):
                    reply, error = generate_reply(done_text, stuck_text)

                duration = ""
                if st.session_state.entered_at:
                    duration = format_duration(time.time() - st.session_state.entered_at)
                st.session_state.last_duration = duration

                lines = [
                    f"📝 **{st.session_state.student_name}** さんの報告",
                ]
                if duration:
                    lines.append(f"⏱ 滞在 {duration}")
                lines += [
                    "",
                    f"**やったこと**\n{done_text}",
                    "",
                    f"**てこずったところ**\n{stuck_text}",
                    "",
                    f"🤖 アシスタントの返信\n{reply}",
                ]
                if error:
                    lines.append(f"\n⚠️ AI呼び出しエラー: {error}")
                send_to_discord("\n".join(lines))

                st.session_state.bot_reply = reply
                st.session_state.mode = "reply"
                st.session_state.entered_at = None
                st.rerun()

    # --- 返信を見せる（v1ではここで即ログアウトしてしまい、生徒は返信を読めなかった）
    elif st.session_state.mode == "reply":
        st.balloons()
        st.markdown("### 📩 アシスタントから")
        st.info(st.session_state.bot_reply)

        if st.session_state.last_duration:
            st.caption(f"今日の滞在時間：{st.session_state.last_duration}")

        st.write("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("もう一度入室する", use_container_width=True):
                st.session_state.mode = "lobby"
                st.rerun()
        with col2:
            if st.button("完全に退室する", use_container_width=True):
                send_to_discord(
                    f"👋 **{st.session_state.student_name}** さんが退室しました。"
                )
                for key, value in defaults.items():
                    st.session_state[key] = value
                st.rerun()
