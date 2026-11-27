"""A geometry-only negative control for the field-label crop classifier."""

import argparse
import json
from pathlib import Path

import numpy as np

from research.ganoderma_crowns import load_tiles
from research.ganoderma_condition import POSITIVE, binary_metrics, cutoff


def geometric_features(root,split):
    rows,labels=[],[]
    for tile in load_tiles(root,split):
        for det,_ in tile.regions:
            x,y=det.center
            a,b,c,d=det.box
            w,h=(c-a)/640,(d-b)/640
            x,y=x/640,y/640
            rows.append([w,h,w*h,abs(x-.5),abs(y-.5),np.hypot(x-.5,y-.5),
                         min(a,b,640-c,640-d)/640])
            labels.append(det.label==POSITIVE)
    return np.asarray(rows),np.asarray(labels,dtype=bool)


def sigmoid(logits):
    return 1/(1+np.exp(-np.clip(logits,-35,35)))


def fit(x,y,penalty):
    beta=np.zeros(x.shape[1])
    weight=np.where(y,np.sqrt((~y).sum()/y.sum()),1.)
    ridge=np.eye(x.shape[1])*penalty*len(y)
    ridge[0,0]=0.
    for _ in range(35):
        p=sigmoid(x@beta)
        gradient=x.T@(weight*(p-y))+ridge@beta
        hessian=x.T@((weight*p*(1-p))[:,None]*x)+ridge
        step=np.linalg.solve(hessian,gradient)
        beta-=step
        if np.linalg.norm(step)<1e-7:
            break
    return beta


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    x,y=geometric_features(args.root,"train")
    v,truth=geometric_features(args.root,"validation")
    mean,sd=x.mean(axis=0),np.maximum(x.std(axis=0),1e-8)
    def design(rows):
        return np.column_stack((np.ones(len(rows)),(rows-mean)/sd))
    candidates=[]
    for penalty in (.0001,.001,.01,.1):
        beta=fit(design(x),y,penalty)
        validation=cutoff(truth,sigmoid(design(v)@beta))
        candidates.append((penalty,beta,validation))
    penalty,coefficients,validation=max(candidates,key=lambda row:row[-1]["average_precision"])
    test,labels=geometric_features(args.root,"test")
    probabilities=sigmoid(design(test)@coefficients)
    result={"scope":"No RGB: box extent and distance to crop centre only","penalty":penalty,
            "validation":validation,"test":binary_metrics(labels,probabilities,validation["threshold"])}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open("x",encoding="utf-8") as file:
        json.dump(result,file,indent=2)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()
