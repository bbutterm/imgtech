import { initializeApp } from 'firebase/app'
import { getAuth } from 'firebase/auth'
import { getFirestore } from 'firebase/firestore'

// публичный конфиг Firebase-проекта (не секрет: он в любом случае
// попадает в браузер каждого пользователя)
const firebaseConfig = {
  apiKey: 'AIzaSyB76GMkZT2mqQtd6IxX0PeF3Y5F2Iusf60',
  authDomain: 'imgtech-820d4.firebaseapp.com',
  projectId: 'imgtech-820d4',
  storageBucket: 'imgtech-820d4.firebasestorage.app',
  messagingSenderId: '287333916548',
  appId: '1:287333916548:web:067385f208860ea6e847d1',
}

const app = initializeApp(firebaseConfig)
export const auth = getAuth(app)
export const db = getFirestore(app)
