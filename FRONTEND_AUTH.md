# 구글 · 카카오 로그인 연동 가이드 (프론트용)

백엔드는 **로그인을 처리하지 않습니다.** Supabase가 신원을 보증하고, 우리 API는
프론트가 보내온 JWT의 서명만 검증합니다. 그래서 프론트가 할 일은 두 가지뿐입니다.

1. `supabase.auth.signInWithOAuth()` 로 구글/카카오 창 띄우기
2. 이후 모든 API 요청에 `Authorization: Bearer <access_token>` 붙이기

회원가입 API는 없습니다. 처음 로그인한 사람은 `GET /api/auth/me` 를 부르는
순간 백엔드 `users` 테이블에 자동 생성됩니다.

---

## 0. 전체 흐름

```
[브라우저]  구글/카카오 로그인 버튼
     │
     ├─ supabase.auth.signInWithOAuth({ provider })
     │        ↓ (구글/카카오 동의 화면)
     │        ↓ https://<ref>.supabase.co/auth/v1/callback
     ├─ 우리 사이트로 리다이렉트 + 세션 저장(localStorage)
     │
     ├─ session.access_token  ← JWT
     │
     └─ fetch('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })
              ↓
        [FastAPI]  JWKS 공개키로 서명 검증 → users 테이블 upsert → 내 정보 반환
```

---

## 1. Supabase 대시보드 설정 (한 번만)

### 1-1. URL Configuration

**Authentication > URL Configuration**

| 항목 | 값 |
|---|---|
| Site URL | `http://localhost:5173` (Vite 기준. 실제 개발 서버 포트로) |
| Redirect URLs | `http://localhost:5173/**` 추가. 배포 주소가 생기면 그것도 추가 |

> 여기에 등록 안 된 주소로 리다이렉트하면 로그인은 성공하는데 세션이 안 잡힙니다.
> "로그인은 되는 것 같은데 로그아웃 상태로 돌아옴" 증상의 90%가 이것입니다.

### 1-2. Google

1. [Google Cloud Console](https://console.cloud.google.com) > API 및 서비스 > 사용자 인증 정보
2. **OAuth 클라이언트 ID 만들기** > 웹 애플리케이션
3. **승인된 리디렉션 URI** 에 이 값을 그대로 넣습니다 (우리 사이트 주소 아님):
   ```
   https://ddcsuwxiwnqrhkjlacnx.supabase.co/auth/v1/callback
   ```
4. 발급된 Client ID / Client Secret 을
   Supabase **Authentication > Sign In / Providers > Google** 에 붙여넣고 Enable

### 1-3. Kakao

1. [Kakao Developers](https://developers.kakao.com) > 내 애플리케이션 > 앱 선택
2. **제품 설정 > 카카오 로그인** → 활성화 ON
3. **Redirect URI** 등록 — 구글과 같은 주소:
   ```
   https://ddcsuwxiwnqrhkjlacnx.supabase.co/auth/v1/callback
   ```
4. **제품 설정 > 카카오 로그인 > 동의항목**
   - 닉네임, 프로필 사진 → 필수 동의
   - 카카오계정(이메일) → 선택 동의
5. **보안 > Client Secret** 생성 후 활성화
6. Supabase **Providers > Kakao** 에
   - `REST API 키` → Client ID 칸
   - `Client Secret` → Client Secret 칸

> ⚠️ **카카오 이메일은 못 받는 경우가 많습니다.** 비즈니스 앱 전환(사업자 정보
> 등록)을 해야 이메일 수집 권한이 나옵니다. 해커톤 기간에는 안 됩니다.
> 백엔드는 이걸 이미 감안해서, 이메일이 없으면 닉네임으로 계정을 만듭니다.
> 그러니 **프론트에서 email이 null이라고 에러 내지 마세요.**

---

## 2. 프론트 코드

### 2-1. 설치

```bash
npm install @supabase/supabase-js
```

### 2-2. 클라이언트

```js
// src/lib/supabase.js
import { createClient } from '@supabase/supabase-js'

export const supabase = createClient(
  import.meta.env.VITE_SUPABASE_URL,      // https://ddcsuwxiwnqrhkjlacnx.supabase.co
  import.meta.env.VITE_SUPABASE_ANON_KEY  // anon / public 키
)

export const API = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
```

`.env.local` (프론트 폴더):

```
VITE_SUPABASE_URL=https://ddcsuwxiwnqrhkjlacnx.supabase.co
VITE_SUPABASE_ANON_KEY=<대시보드 Project Settings > API Keys > anon public>
VITE_API_BASE=http://localhost:8000
```

> 값을 직접 받기 귀찮으면 백엔드가 내려줍니다: `GET /api/auth/config` →
> `{ supabase_url, supabase_anon_key, auth_required, ready }`

### 2-3. 로그인 / 로그아웃

```js
// 구글
await supabase.auth.signInWithOAuth({
  provider: 'google',
  options: { redirectTo: window.location.origin },
})

// 카카오
await supabase.auth.signInWithOAuth({
  provider: 'kakao',
  options: { redirectTo: window.location.origin },
})

// 로그아웃
await supabase.auth.signOut()
```

### 2-4. 세션 구독 + 백엔드 유저 동기화

```js
import { useEffect, useState } from 'react'
import { supabase, API } from './lib/supabase'

export function useAuth() {
  const [session, setSession] = useState(null)
  const [me, setMe] = useState(null)   // 백엔드 users 레코드 (id, point_balance 등)

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session))
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setSession(s))
    return () => sub.subscription.unsubscribe()
  }, [])

  useEffect(() => {
    if (!session) { setMe(null); return }
    fetch(`${API}/api/auth/me`, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    })
      .then(r => r.json())
      .then(setMe)
  }, [session])

  return { session, me, user: session?.user ?? null }
}
```

### 2-5. API 호출 헬퍼

토큰은 1시간이면 만료됩니다. **저장해두지 말고 매 호출마다 `getSession()` 으로
꺼내세요.** supabase-js가 알아서 갱신해줍니다.

```js
export async function apiFetch(path, options = {}) {
  const { data: { session } } = await supabase.auth.getSession()

  const res = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}),
      ...options.headers,
    },
  })

  if (!res.ok) throw new Error((await res.json()).detail ?? res.statusText)
  return res.json()
}
```

### 2-6. 리뷰 등록 (로그인 필수 엔드포인트)

```js
// 작성자 정보는 토큰에서 꺼내므로 user_id를 보내지 않습니다.
const result = await apiFetch('/api/reviews', {
  method: 'POST',
  body: JSON.stringify({
    cafe_id: 17,
    rating: 5,
    content: '콘센트 자리마다 있고 사람 없어서 3시간 작업했어요',
    tags: ['콘센트많음', '조용함'],
  }),
})
// → { review: {...}, earned_point: 320, breakdown: {...}, point_balance: 320 }
```

로그인 필수: `POST /api/reviews`, `POST /api/reports`, `GET /api/auth/me`,
`GET /api/users/me/*`
로그인 없이 가능: `GET /api/cafes`, `GET /api/stats/*`, `GET /api/meta/hotspots`

---

## 3. 자주 나오는 에러

| 증상 | 원인 | 해결 |
|---|---|---|
| 401 `로그인이 필요합니다` | 헤더 안 붙음 | `Authorization: Bearer <access_token>`. `session.user.id` 가 아니라 `session.access_token` 입니다 |
| 401 `토큰이 만료됐습니다` | 토큰을 state에 캐싱함 | 호출 직전에 `getSession()` 으로 다시 꺼내기 |
| 500 `레거시 HS256 토큰` | 구 프로젝트 | 백엔드 `.env` 에 `SUPABASE_JWT_SECRET` 추가 |
| 로그인 후 로그아웃 상태로 돌아옴 | Redirect URL 미등록 | Authentication > URL Configuration 에 개발 서버 주소 추가 |
| CORS 에러 | 백엔드 `CORS_ORIGINS` | `.env` 에 `CORS_ORIGINS=http://localhost:5173` |
| `provider is not enabled` | Supabase에서 Provider Enable 안 함 | Providers 화면에서 토글 ON + 저장 |
| 카카오 로그인 후 email이 null | 비즈앱 미전환 | 정상입니다. 닉네임으로 계정 생성됨 |

디버깅 시작점: **`GET /api/auth/config`** 를 먼저 열어보세요.
`ready: false` 면 백엔드 `.env` 문제, `true` 인데 401이면 프론트 헤더 문제입니다.
