/* tgen.h -- exact port of Terraria 1.4.5 worldgen RNG up to and including TerrainPass.
 * Ported 1:1 from decompiled Terraria.WorldGen.Reset / GenerateWorld / TerrainPass.
 * Every RNG draw is in the original order; do not reorder.
 */
#ifndef TGEN_H
#define TGEN_H
#include <string.h>

typedef struct { int S[56]; unsigned inext; } UR;

static void ur_seed(UR*r,int Seed){
    int num = (Seed==(-2147483647-1)) ? 2147483647 : (Seed<0?-Seed:Seed);
    int num2 = 161803398 - num;
    r->S[55]=num2; int num3=1;
    for(int j=1;j<55;j++){ int n4=21*j%55; r->S[n4]=num3; num3=num2-num3;
        if(num3<0)num3+=2147483647; num2=r->S[n4]; }
    for(int k=1;k<5;k++) for(int l=1;l<56;l++){
        r->S[l]-=r->S[1+(l+30)%55]; if(r->S[l]<0)r->S[l]+=2147483647; }
    r->inext=0;
}
static inline int ur_raw(UR*r){
    unsigned n=r->inext+1; if(n>55)n=1;
    unsigned n2=n+21; if(n2>55)n2-=55;
    int a=r->S[n]-r->S[n2];
    if(a==2147483647)a--;
    a=(r->S[n]=a+((a>>31)&0x7FFFFFFF));
    r->inext=n; return a;
}
/* C# constant 4.656612875245797E-10 is exactly 1/(2^31-1), so integer division
 * by 2147483647 is bit-identical (verified over 135M samples). */
static inline int ur_next(UR*r,int mx){
    return (int)(((long long)ur_raw(r)*(long long)mx)/2147483647LL); }
static inline int ur_next2(UR*r,int mn,int mx){
    long long n=(long long)mx-(long long)mn;
    return (int)(((long long)ur_raw(r)*n)/2147483647LL)+mn; }

typedef struct {
    int maxTilesX, maxTilesY;
    int getGood, drunk, remix, anniversary, notTheBees, dontStarve, zenith;
} WorldOpt;

static inline int world_size(int mx){ return (mx<=4200)?0:((mx<=6400)?1:2); }

enum { FT_Plateau=0, FT_Hill, FT_Dale, FT_Mountain, FT_Valley };

static double gen_surface_offset(UR*r,int featureType,const WorldOpt*w){
    double num=0.0;
    if((w->drunk||w->getGood||w->remix) && ur_next(r,2)==0){
        switch(featureType){
        case FT_Plateau:  while(ur_next2(r,0,6)==0) num+=(double)ur_next2(r,-1,2); break;
        case FT_Hill:     while(ur_next2(r,0,3)==0) num-=1.0;
                          while(ur_next2(r,0,10)==0) num+=1.0; break;
        case FT_Dale:     while(ur_next2(r,0,3)==0) num+=1.0;
                          while(ur_next2(r,0,10)==0) num-=1.0; break;
        case FT_Mountain: while(ur_next2(r,0,3)!=0) num-=1.0;
                          while(ur_next2(r,0,6)==0) num+=1.0; break;
        case FT_Valley:   while(ur_next2(r,0,3)!=0) num+=1.0;
                          while(ur_next2(r,0,5)==0) num-=1.0; break;
        }
    } else {
        switch(featureType){
        case FT_Plateau:  while(ur_next2(r,0,7)==0) num+=(double)ur_next2(r,-1,2); break;
        case FT_Hill:     while(ur_next2(r,0,4)==0) num-=1.0;
                          while(ur_next2(r,0,10)==0) num+=1.0; break;
        case FT_Dale:     while(ur_next2(r,0,4)==0) num+=1.0;
                          while(ur_next2(r,0,10)==0) num-=1.0; break;
        case FT_Mountain: while(ur_next2(r,0,2)==0) num-=1.0;
                          while(ur_next2(r,0,6)==0) num+=1.0; break;
        case FT_Valley:   while(ur_next2(r,0,2)==0) num+=1.0;
                          while(ur_next2(r,0,5)==0) num-=1.0; break;
        }
    }
    return num;
}

typedef struct {
    int jungleHut, crimsonLeft, worldID;
    int treeX[3], treeStyle[4];
    int caveBackX[3], caveBackStyle[4];
    int bg[4];
    int corruptBG, jungleBG, snowBG, hallowBG, crimsonBG, desertBG,
        oceanBG, mushroomBG, underworldBG;
    int moon;
    int dungeonSide;
    int leftBeachEnd, rightBeachStart;
    /* Ore tiers: 0 = the base ore (copper/iron/silver/gold), 1 = the alternate
     * (tin/lead/tungsten/platinum). Four independent coin flips, so naming all
     * four cuts the search space 16x. */
    int oreCopper, oreIron, oreSilver, oreGold;
} PreGen;

static int roll_forest_bg(UR*r){
    /* WorldGen.RollRandomForestBGStyle, complete. The 5->51 and 7->71..73
     * promotions each consume a draw, so omitting them desyncs every later
     * roll, not just the background value. */
    int maxValue=14;
    int num=ur_next(r,maxValue);
    if((num==1||num==2)&&ur_next(r,2)==0) num=ur_next(r,maxValue);
    if(num==0) num=ur_next(r,maxValue);
    if(num==3&&ur_next(r,3)==0) num=31;
    if(num==5&&ur_next(r,2)==0) num=51;
    if(num==7&&ur_next(r,4)==0) num=ur_next2(r,71,74);
    return num;
}

static inline int zone_count(int mx){ int s=world_size(mx); return (s==0)?2:((s==1)?3:4); }

static void pregen(UR*r,const WorldOpt*w,PreGen*g){
    const int MXW=w->maxTilesX;
    /* WorldGen.Reset consumes exactly 17 draws between seeding Main.rand and
     * Main.ActiveWorldFileData.WorldId. This used to be modelled as an opaque
     * 15-draw preamble, which gave the right count but the wrong order and left
     * nowhere to express the Constant ore skip. Spelled out, verified against
     * two real world files whose headers store the generated values:
     *   seed 1          4200x1200 ftw=0 -> worldId 1321446349, treeX[0] 2528
     *   seed 853251839  8400x2400 ftw=1 -> worldId 2111289402, treeX[0] 2379 */
    g->jungleHut = ur_next(r,5);
    g->crimsonLeft = (ur_next(r,2)==0)?0:1;

    /* RandomizeWeather: numClouds, then the windSpeed loop, which always runs
     * exactly once (NextFloat is zero with probability 2^-24). */
    ur_next2(r,10,200); ur_raw(r); ur_next(r,2);

    /* GenVars.hellChestItem: a 5-item list drained one random index at a time. */
    for(int n=5;n>0;n--) ur_next(r,n);

    ur_raw(r);   /* Main.slimeRainTime  */
    ur_raw(r);   /* Main.cloudBGActive  */

    /* Ore tier rolls. The Constant short-circuits the iron and gold guards
     * (`(!dontStarveWorldGen || drunkWorldGen) && genRand.Next(2) == 0`), so
     * with dontStarve on and drunk off those two draws never happen and the
     * whole downstream stream shifts by two. */
    g->oreCopper = (ur_next(r,2)==0)?1:0;
    /* The Constant skips these two entirely, so those worlds are always
     * base-tier iron and gold and the draws never happen. */
    g->oreIron   = (!w->dontStarve || w->drunk) ? ((ur_next(r,2)==0)?1:0) : 0;
    g->oreSilver = (ur_next(r,2)==0)?1:0;
    g->oreGold   = (!w->dontStarve || w->drunk) ? ((ur_next(r,2)==0)?1:0) : 0;

    ur_next(r,2);   /* WorldGen.crimson */
    g->worldID = ur_next(r,2147483647);

    if(MXW==4200){
        g->treeX[0]=ur_next2(r,(int)(MXW*0.5-MXW*0.25),(int)(MXW*0.5+MXW*0.25));
        g->treeStyle[0]=ur_next(r,6); g->treeStyle[1]=ur_next(r,6);
        while(g->treeStyle[1]==g->treeStyle[0]) g->treeStyle[1]=ur_next(r,6);
        g->treeX[1]=MXW; g->treeX[2]=MXW;
        for(int i=0;i<2;i++) if(g->treeStyle[i]==0&&ur_next(r,3)!=0) g->treeStyle[i]=4;
        g->treeStyle[2]=g->treeStyle[3]=-1;
    } else if(MXW==6400){
        g->treeX[0]=ur_next2(r,(int)(MXW*0.334-MXW*0.2),(int)(MXW*0.334+MXW*0.2));
        g->treeX[1]=ur_next2(r,(int)(MXW*0.667-MXW*0.2),(int)(MXW*0.667+MXW*0.2));
        g->treeStyle[0]=ur_next(r,6); g->treeStyle[1]=ur_next(r,6); g->treeStyle[2]=ur_next(r,6);
        while(g->treeStyle[1]==g->treeStyle[0]) g->treeStyle[1]=ur_next(r,6);
        while(g->treeStyle[2]==g->treeStyle[0]||g->treeStyle[2]==g->treeStyle[1]) g->treeStyle[2]=ur_next(r,6);
        g->treeX[2]=MXW;
        for(int j=0;j<3;j++) if(g->treeStyle[j]==0&&ur_next(r,3)!=0) g->treeStyle[j]=4;
        g->treeStyle[3]=-1;
    } else {
        g->treeX[0]=ur_next2(r,(int)(MXW*0.25-MXW*0.15),(int)(MXW*0.25+MXW*0.15));
        g->treeX[1]=ur_next2(r,(int)(MXW*0.5 -MXW*0.15),(int)(MXW*0.5 +MXW*0.15));
        g->treeX[2]=ur_next2(r,(int)(MXW*0.75-MXW*0.15),(int)(MXW*0.75+MXW*0.15));
        g->treeStyle[0]=ur_next(r,6); g->treeStyle[1]=ur_next(r,6);
        g->treeStyle[2]=ur_next(r,6); g->treeStyle[3]=ur_next(r,6);
        while(g->treeStyle[1]==g->treeStyle[0]) g->treeStyle[1]=ur_next(r,6);
        while(g->treeStyle[2]==g->treeStyle[0]||g->treeStyle[2]==g->treeStyle[1]) g->treeStyle[2]=ur_next(r,6);
        while(g->treeStyle[3]==g->treeStyle[0]||g->treeStyle[3]==g->treeStyle[1]||g->treeStyle[3]==g->treeStyle[2])
            g->treeStyle[3]=ur_next(r,6);
        for(int k=0;k<4;k++) if(g->treeStyle[k]==0&&ur_next(r,3)!=0) g->treeStyle[k]=4;
    }

    {
        int mv=8;
        if(MXW==4200){
            g->caveBackX[0]=ur_next2(r,(int)(MXW*0.5-MXW*0.25),(int)(MXW*0.5+MXW*0.25));
            g->caveBackX[1]=MXW; g->caveBackX[2]=MXW;
            g->caveBackStyle[0]=ur_next(r,mv); g->caveBackStyle[1]=ur_next(r,mv);
            while(g->caveBackStyle[1]==g->caveBackStyle[0]) g->caveBackStyle[1]=ur_next(r,mv);
            g->caveBackStyle[2]=g->caveBackStyle[3]=-1;
        } else if(MXW==6400){
            g->caveBackX[0]=ur_next2(r,(int)(MXW*0.334-MXW*0.2),(int)(MXW*0.334+MXW*0.2));
            g->caveBackX[1]=ur_next2(r,(int)(MXW*0.667-MXW*0.2),(int)(MXW*0.667+MXW*0.2));
            g->caveBackX[2]=MXW;
            g->caveBackStyle[0]=ur_next(r,mv); g->caveBackStyle[1]=ur_next(r,mv); g->caveBackStyle[2]=ur_next(r,mv);
            while(g->caveBackStyle[1]==g->caveBackStyle[0]) g->caveBackStyle[1]=ur_next(r,mv);
            while(g->caveBackStyle[2]==g->caveBackStyle[0]||g->caveBackStyle[2]==g->caveBackStyle[1])
                g->caveBackStyle[2]=ur_next(r,mv);
            g->caveBackStyle[3]=-1;
        } else {
            g->caveBackX[0]=ur_next2(r,(int)(MXW*0.25-MXW*0.15),(int)(MXW*0.25+MXW*0.15));
            g->caveBackX[1]=ur_next2(r,(int)(MXW*0.5 -MXW*0.15),(int)(MXW*0.5 +MXW*0.15));
            g->caveBackX[2]=ur_next2(r,(int)(MXW*0.75-MXW*0.15),(int)(MXW*0.75+MXW*0.15));
            g->caveBackStyle[0]=ur_next(r,mv); g->caveBackStyle[1]=ur_next(r,mv);
            g->caveBackStyle[2]=ur_next(r,mv); g->caveBackStyle[3]=ur_next(r,mv);
            while(g->caveBackStyle[1]==g->caveBackStyle[0]) g->caveBackStyle[1]=ur_next(r,mv);
            while(g->caveBackStyle[2]==g->caveBackStyle[0]||g->caveBackStyle[2]==g->caveBackStyle[1])
                g->caveBackStyle[2]=ur_next(r,mv);
            while(g->caveBackStyle[3]==g->caveBackStyle[0]||g->caveBackStyle[3]==g->caveBackStyle[1]
                  ||g->caveBackStyle[3]==g->caveBackStyle[2]) g->caveBackStyle[3]=ur_next(r,mv);
        }
        /* Tail of RandomizeCaveBackgrounds, outside the size branches and so
         * run for every world: iceBackStyle, hellBackStyle, jungleBackStyle. */
        ur_next(r,4);
        ur_next(r,3);
        ur_next(r,2);
    }

    g->bg[0]=roll_forest_bg(r);
    do { g->bg[1]=roll_forest_bg(r); } while(g->bg[1]==g->bg[0]);
    g->bg[2]=roll_forest_bg(r);
    while(g->bg[2]==g->bg[0]||g->bg[2]==g->bg[1]) g->bg[2]=roll_forest_bg(r);
    g->bg[3]=roll_forest_bg(r);
    while(g->bg[3]==g->bg[0]||g->bg[3]==g->bg[1]||g->bg[3]==g->bg[2]) g->bg[3]=roll_forest_bg(r);

    /* Remaining RandomizeBackgrounds rolls. These MUST be replayed even though
     * only a few are used as clues, or every later draw desyncs. Order is
     * exactly as in WorldGen.RandomizeBackgrounds. */
    {
        /* setBG(1, RandomizeBackgrounds_Corruption) */
        int c = ur_next(r,6);
        if(c==5) c = (ur_next(r,2)==0) ? 51 : 52;
        g->corruptBG = c;
        /* setBG(2, random.Next(7)) -- jungle */
        g->jungleBG = ur_next(r,7);
        /* snow, with its three nested promotion checks */
        int s = ur_next(r,9);
        if(s==2 && ur_next(r,2)==0) s = (ur_next(r,2)==0) ? 21 : 22;
        if(s==3 && ur_next(r,2)==0) s = (ur_next(r,2)==0) ? 31 : 32;
        if(s==4 && ur_next(r,2)==0) s = (ur_next(r,2)==0) ? 41 : 42;
        g->snowBG = s;
        /* setBG(4, Next(6)) hallow, setBG(5, Next(7)) crimson */
        g->hallowBG = ur_next(r,6);
        g->crimsonBG = ur_next(r,7);
        /* setBG(6, RandomizeBackgrounds_Desert) */
        int d = ur_next(r,6);
        if(d==5){ int d2=ur_next(r,5); d = 51 + d2/2; }
        g->desertBG = d;
        /* setBG(7, Next(8)) -- ocean */
        g->oceanBG = ur_next(r,8);
        /* setBG(8, Next(5)) mushroom, setBG(9, Next(3)) underworld */
        g->mushroomBG = ur_next(r,5);
        g->underworldBG = ur_next(r,3);
    }

    g->moon = ur_next(r,9);

    int side = (ur_next(r,2)==0)?0:1;
    g->dungeonSide = side;

    {
        int minV=15,maxV=30;
        if(w->anniversary && !w->remix){ minV=25; maxV=35; }
        if(w->remix){ ur_next2(r,20,35); }
        else { ur_next2(r,minV,maxV); }
    }

    {
        int num7=ur_next(r,MXW);
        if(side==1){ while(num7<MXW*0.6||num7>MXW*0.75) num7=ur_next(r,MXW); }
        else       { while(num7<MXW*0.25||num7>MXW*0.4) num7=ur_next(r,MXW); }
        double num9=(double)MXW/4200.0;
        int num8=ur_next2(r,50,90);
        num8+=(int)((double)ur_next2(r,20,40)*num9);
        num8+=(int)((double)ur_next2(r,20,40)*num9);
        num8=ur_next2(r,50,90);
        num8+=(int)((double)ur_next2(r,20,40)*num9);
        num8+=(int)((double)ur_next2(r,20,40)*num9);
        (void)num8;
    }

    {
        const int beachBordersWidth=275;
        const int beachSandRandomCenter=beachBordersWidth+5+40;
        const int beachSandRandomWidthRange=20;
        const int beachSandDungeonExtraWidth=40;
        const int beachSandJungleExtraWidth=20;
        int lbe=ur_next2(r,beachSandRandomCenter-beachSandRandomWidthRange,
                           beachSandRandomCenter+beachSandRandomWidthRange);
        if(w->anniversary&&!w->remix) lbe=beachSandRandomCenter+beachSandRandomWidthRange;
        lbe += (side==1)?beachSandDungeonExtraWidth:beachSandJungleExtraWidth;
        int rbs=MXW-ur_next2(r,beachSandRandomCenter-beachSandRandomWidthRange,
                               beachSandRandomCenter+beachSandRandomWidthRange);
        if(w->anniversary&&!w->remix) rbs=MXW-(beachSandRandomCenter+beachSandRandomWidthRange);
        rbs -= (side==1)?beachSandJungleExtraWidth:beachSandDungeonExtraWidth;
        g->leftBeachEnd=lbe; g->rightBeachStart=rbs;
    }
}

/* ---------- TerrainPass.ApplyPass surface walk (exact) ----------
 * Writes surface height per column into out[0..maxTilesX-1].
 * FlatBeachPadding is 5 in vanilla GameConfiguration.
 */
/* WorldGenerator.RunPass does `Main.rand = new UnifiedRandom(_seed)` before
 * EVERY pass, so TerrainPass starts from a freshly seeded generator rather
 * than continuing the pregen stream. The caller must pass the world seed. */
static void terrain_pass_seeded(int seed,const WorldOpt*w,const PreGen*g,double*out);
/* Back-compat wrapper: callers that already hold a UR are given the seeded
   behaviour instead, since a continued stream is never correct here. */
static void terrain_pass_seeded(int seed,const WorldOpt*w,const PreGen*g,double*out){
    UR rr; ur_seed(&rr,seed);
    UR*r=&rr;
    const int MXW=w->maxTilesX, MYW=w->maxTilesY;
    const int pad=5;
    int featureType=FT_Plateau;
    int num2=0;
    double num3=(double)MYW*0.3;
    num3*=(double)ur_next2(r,90,110)*0.005;
    double num4=num3+(double)MYW*0.2;
    num4*=(double)ur_next2(r,90,110)*0.01;
    if(w->remix){
        num4=(double)MYW*0.5;
        if(MXW>2500) num4=(double)MYW*0.6;
        num4*=(double)ur_next2(r,95,106)*0.01;
    }
    double num9=(double)MYW*0.23;
    double hist[500];
    for(int q=0;q<500;q++) hist[q]=0.0;
    num2=g->leftBeachEnd+pad;
    for(int i=0;i<MXW;i++){
        if(num2<=0){
            featureType=ur_next2(r,0,5);
            num2=ur_next2(r,5,40);
            if(featureType==FT_Plateau) num2*=(int)((double)ur_next2(r,5,30)*0.2);
        }
        num2--;
        if((double)i>(double)MXW*0.45 && (double)i<(double)MXW*0.55
           && (featureType==FT_Mountain||featureType==FT_Valley))
            featureType=ur_next2(r,0,3);
        if((double)i>(double)MXW*0.48 && (double)i<(double)MXW*0.52)
            featureType=FT_Plateau;
        num3 += gen_surface_offset(r,featureType,w);
        double num10=0.17, num11=0.26;
        if(w->drunk){ num10=0.15; num11=0.28; }
        if(world_size(MXW)==0) num10+=0.02;
        if(i<g->leftBeachEnd+pad || i>g->rightBeachStart-pad){
            double lo=(double)MYW*num10;
            /* Utils.Clamp tests MAX first, then min, so the order matters
             * whenever lo > num9 (the beach ceiling sits below the floor). */
            if(num3>num9)num3=num9; else if(num3<lo)num3=lo;
        } else if(num3<(double)MYW*num10){ num3=(double)MYW*num10; num2=0; }
        else if(num3>(double)MYW*num11){ num3=(double)MYW*num11; num2=0; }
        while(ur_next2(r,0,3)==0){ num4+=(double)ur_next2(r,-2,3); }
        if(w->remix){
            if(MXW>2500){ if(num4>(double)MYW*0.7) num4-=1.0; }
            else { if(num4>(double)MYW*0.6) num4-=1.0; }
        } else {
            if(num4<num3+(double)MYW*0.06) num4+=1.0;
            if(num4>num3+(double)MYW*0.35) num4-=1.0;
        }
        out[i]=num3;
        /* SurfaceHistory records every column; the right-beach retarget below
         * rewrites the last 500 of them, so they must be kept. */
        hist[i % 500] = num3;
        if(i==g->rightBeachStart-pad){
            /* TerrainPass.RetargetSurfaceHistory: when the surface is still
             * below the beach line, the previous 500 columns are pulled down
             * one tile at a time until they reach it, then rewritten. Without
             * this the whole right-hand approach to the ocean stays too high. */
            if(num3>num9){
                int H=500;
                #define HH(k) hist[(i - (k)) % 500 < 0 ? ((i-(k))%500+500) : (i-(k))%500]
                for(int a=0;a<H/2;a++){
                    if(HH(0)<=num9) break;
                    for(int b=0;b<H-a*2;b++){
                        double v=HH(b)-1.0;
                        hist[((i-b)%500+500)%500]=v;
                        if(v<=num9) break;
                    }
                }
                for(int k=0;k<H;k++){
                    int col=i-k;
                    if(col<0) break;
                    out[col]=hist[((col)%500+500)%500];
                }
                #undef HH
            }
            featureType=FT_Plateau;
            num2=MXW-i;
        }
    }
}
#endif
