
import argparse, os, json, math, random
import numpy as np, pandas as pd, torch, torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.metrics import classification_report
from tqdm import tqdm
from utils import LABEL2ID, ID2LABEL, plot_training_curves, plot_confusion_matrix, plot_roc

class TextDS(Dataset):
    def __init__(self, df, vocab=None, max_len=64, build=False, min_freq=1):
        self.texts = df["text"].tolist()
        self.labels = [LABEL2ID[l] for l in df["label"].tolist()]
        self.max_len = max_len
        if build or vocab is None:
            self.vocab = {"<pad>":0, "<unk>":1}
            from collections import Counter
            cnt = Counter()
            for t in self.texts:
                cnt.update(t.split())
            for w, c in cnt.items():
                if c >= min_freq and w not in self.vocab:
                    self.vocab[w] = len(self.vocab)
        else:
            self.vocab = vocab

    def encode(self, s):
        ids = [self.vocab.get(w, 1) for w in s.split()][:self.max_len]
        if len(ids) < self.max_len:
            ids += [0] * (self.max_len - len(ids))
        return ids

    def __len__(self): return len(self.texts)
    def __getitem__(self, idx):
        return torch.tensor(self.encode(self.texts[idx]), dtype=torch.long), torch.tensor(self.labels[idx], dtype=torch.long)

class LSTMClassifier(nn.Module):
    def __init__(self, vocab_size, embed_dim=100, hidden_dim=128, num_classes=3):
        super().__init__()
        self.emb = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        self.lstm = nn.LSTM(embed_dim, hidden_dim, batch_first=True, bidirectional=True)
        self.fc = nn.Linear(hidden_dim*2, num_classes)
        self.drop = nn.Dropout(0.3)

    def forward(self, x):
        e = self.emb(x)
        out,_ = self.lstm(e)
        feat = torch.mean(out, dim=1)
        return self.fc(self.drop(feat))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", required=True)
    ap.add_argument("--val", required=True)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--embed-dim", type=int, default=100)
    ap.add_argument("--hidden-dim", type=int, default=128)
    ap.add_argument("--max-len", type=int, default=64)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    torch.manual_seed(args.seed); np.random.seed(args.seed); random.seed(args.seed)

    tr = pd.read_csv(args.train); va = pd.read_csv(args.val)
    trds = TextDS(tr, build=True, max_len=args.max_len, min_freq=1)
    vads = TextDS(va, vocab=trds.vocab, max_len=args.max_len)

    train_loader = DataLoader(trds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(vads, batch_size=args.batch_size, shuffle=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = LSTMClassifier(len(trds.vocab), args.embed_dim, args.hidden_dim).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    crit = nn.CrossEntropyLoss()

    history = {"train_loss":[], "val_loss":[]}
    best_val = 1e9; best_path = os.path.join(args.outdir, "best_model.pt")
    for ep in range(1, args.epochs+1):
        model.train(); tl=0;n=0
        for xb,yb in tqdm(train_loader, desc=f"Epoch {ep}/{args.epochs} [train]"):
            xb,yb = xb.to(device), yb.to(device)
            opt.zero_grad(); logits = model(xb)
            loss = crit(logits, yb)
            loss.backward(); opt.step()
            tl += loss.item()*xb.size(0); n+=xb.size(0)
        tr_loss = tl/n

        model.eval(); vl=0; vn=0; y_true=[]; y_pred=[]; y_prob=[]
        with torch.no_grad():
            for xb,yb in tqdm(val_loader, desc=f"Epoch {ep}/{args.epochs} [val]"):
                xb,yb = xb.to(device), yb.to(device)
                logits = model(xb)
                loss = crit(logits, yb)
                vl += loss.item()*xb.size(0); vn+=xb.size(0)
                probs = torch.softmax(logits, dim=1)
                y_prob.append(probs.cpu().numpy())
                y_true.extend(yb.cpu().numpy().tolist())
                y_pred.extend(probs.argmax(dim=1).cpu().numpy().tolist())
        val_loss = vl/vn
        history["train_loss"].append(tr_loss); history["val_loss"].append(val_loss)
        print(f"[epoch {ep}] train_loss={tr_loss:.4f} val_loss={val_loss:.4f}")

        if val_loss < best_val:
            best_val = val_loss
            torch.save({"state_dict":model.state_dict(),
                        "vocab":trds.vocab,
                        "max_len":args.max_len,
                        "model_type":"lstm"}, best_path)
        plot_training_curves(history, os.path.join(args.outdir, "training_curves.png"))

    print("[OK] Training complete. Best saved to", best_path)

if __name__ == "__main__":
    main()
